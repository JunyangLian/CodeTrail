"""P1.3 driver: one full offline evolution cycle over REAL historical traces.

Chain: regression gate -> data flywheel -> failure analyzer -> strategy
proposer -> shadow A/B (mock) -> promotion gate. Zero model API calls;
every stage labelled with its evidence source.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent.evolution.evolution_loop import run_loop
from genomeqc_agent.evolution.prompts import PromptRegistry
from genomeqc_agent.model.scripted_planner import (
    PlannedAction,
    ScriptedPlanner,
    evidence_pointers,
)
from genomeqc_agent.runtime.graph import QCGraph

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "evolution_state"
EXAMPLES = ROOT / "examples" / "public_packets"


class FaultyStubPlanner(ScriptedPlanner):
    """Mock shadow runner planner with the real hallucinated-file failure
    shape (golden case CHLORO-CDS-03)."""

    def __init__(self):
        super().__init__()
        self.hallucinated = "proteins.fa:INVENTED-LOCATOR"

    def next_action(self, facts: dict | None, context: dict) -> PlannedAction:
        denial = (context.get("denial") or {}).get("code", "")
        trace = context.get("tool_trace") or {}
        if denial == "pointer_not_in_manifest":
            return PlannedAction(kind="tool", tool_name="verify_evidence_pointer",
                                 arguments={"pointers": evidence_pointers(facts or {})[:50]},
                                 reason="replan: manifest files only")
        if "verify_evidence_pointer" not in trace:
            pointers = [self.hallucinated] + evidence_pointers(facts or {})[:49]
            return PlannedAction(kind="tool", tool_name="verify_evidence_pointer",
                                 arguments={"pointers": pointers},
                                 reason="shadow verify (hallucination injected)")
        return PlannedAction(kind="synthesize", reason="done")


def shadow_runner(receipts_dir: Path, proposal: dict) -> list[dict]:
    """Mock shadow execution of the candidate strategy on two packets."""
    registry = PromptRegistry(STATE / "prompt_registry.v1.json")
    strategy = {"prompt_version": "promptv2",
                "prompt_sha256": registry.get("promptv2")["sha256"],
                "policy_guards": dict(proposal.get("config_delta") or {})}
    runs = []
    for packet_id in ("chloro_C1_NC_013991_2_CHLORO-CDS-03",
                      "chloro_C1_NC_013991_2_CHLORO-NORMAL-00"):
        graph = QCGraph(planner=FaultyStubPlanner())
        try:
            state = graph.run(str(uuid.uuid4()), packet_id,
                              str(EXAMPLES / packet_id), strategy=strategy)
        finally:
            graph.close()
        runs.append({
            "run_id": state["run_id"], "packet_id": packet_id,
            "status": state["runtime"]["status"],
            "decision": (state.get("result") or {}).get("qc_decision"),
            "evidence_validity": (state.get("result") or {}).get("evidence_validity"),
            "model_output_ref": f"mock://{state['run_id']}/QC_REPORT.json",
        })
    return runs


def main() -> int:
    entries = [json.loads(line) for line in
               (STATE / "failure_pool" / "failure_cases.v1.jsonl")
               .read_text(encoding="utf-8").splitlines() if line.strip()]
    report = run_loop(
        entries, ROOT / "data" / "evolution" / "loop",
        active_config={"max_tokens": 8192, "gateway_timeout_s": 120,
                       "verify_pointer_cap": 50},
        shadow_runner=shadow_runner, tests_root=ROOT,
        pool_path=ROOT / "data" / "regression_pool" / "v3_pool.v1.jsonl")
    print("verdict:", report["verdict"])
    for stage, payload in report["stages"].items():
        print(f"  {stage}: {json.dumps(payload, ensure_ascii=False)[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
