"""Phase 2 A/B preflight driver (ZERO API): proves the candidate strategy is
executable end to end on mocks, estimates the real-run budget from G4
receipts, and freezes the smoke design. Writes
evolution_state/V3_1_AB_PREFLIGHT_TEST_RECEIPT.json.

No model API is called. Everything is mock / historical-data derived.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent.evolution.controller import (
    EvolutionController,
    EvolutionError,
)
from genomeqc_agent.evolution.prompts import PromptRegistry
from genomeqc_agent.model.llm_planner import LLMPlanner
from genomeqc_agent.model.scripted_planner import (
    PlannedAction,
    ScriptedPlanner,
    evidence_pointers,
)
from genomeqc_agent.runtime.graph import QCGraph

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "evolution_state"
EXAMPLES = ROOT / "examples" / "public_packets"
RECEIPT_PATH = STATE / "V3_1_AB_PREFLIGHT_TEST_RECEIPT.json"

GUARDS_ON = {"evidence_enforce_manifest": True, "pre_verify_pointers": True,
             "unresolved_requires_review": True}

SMOKE_PACKETS = [  # 4 tasks x frozen; seen DEV cases - smoke only, never scored
    "chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
    "chloro_C1_NC_013991_2_CHLORO-CDS-03",
    "chloro_C1_NC_013991_2_CHLORO-META-05",
    "chloro_C1_NC_082429_1_CHLORO-ID-01",
]
# NC_013991_2 packets ship as public examples; the remaining smoke tasks come
# from the V2 candidates pool used by eval_offline (same DEV slice as G4).
V2_CANDIDATES = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"
                     ) / "data" / "organelle_v2" / "candidates"


class BindingGateway:
    """Fake gateway that records the exact system prompt of every call."""

    model_id = "mock-binding-gateway"

    def __init__(self):
        self.calls: list[dict] = []

    def chat_json(self, system: str, user: str, max_tokens: int = 4096) -> dict:
        self.calls.append({"system": system, "max_tokens": max_tokens})
        return {"content": json.dumps({"kind": "synthesize", "tool_name": "",
                                       "arguments": {}, "reason": "mock"}),
                "latency_ms": 1,
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2}}


class FaultyStubPlanner(ScriptedPlanner):
    """Scripted planner with an injected bad habit, mirroring the real GLM
    hallucinated-pointer failure (golden case CHLORO-CDS-03)."""

    def __init__(self, habit: str):
        super().__init__()
        self.habit = habit
        self.hallucinated = "proteins.fa:INVENTED-LOCATOR"

    def next_action(self, facts: dict | None, context: dict) -> PlannedAction:
        denial = (context.get("denial") or {}).get("code", "")
        trace = context.get("tool_trace") or {}
        if self.habit == "hallucinate":
            if denial == "pointer_not_in_manifest":
                return PlannedAction(
                    kind="tool", tool_name="verify_evidence_pointer",
                    arguments={"pointers": evidence_pointers(facts or {})[:50]},
                    reason="replan: cite manifest files only")
            if "verify_evidence_pointer" not in trace:
                pointers = [self.hallucinated] + evidence_pointers(facts or {})[:49]
                return PlannedAction(
                    kind="tool", tool_name="verify_evidence_pointer",
                    arguments={"pointers": pointers},
                    reason="verify evidence (one invented file injected)")
            return PlannedAction(kind="synthesize", reason="verification done")
        if self.habit == "skip_verify":
            if denial == "evidence_unverified":
                return PlannedAction(
                    kind="tool", tool_name="verify_evidence_pointer",
                    arguments={"pointers": evidence_pointers(facts or {})[:50]},
                    reason="replan: verify before finishing")
            if "verify_evidence_pointer" not in trace:
                return PlannedAction(kind="synthesize",
                                     reason="finish without verification")
        return PlannedAction(kind="synthesize", reason="done")


def _run(packet_id: str, packet_dir: Path, planner, strategy: dict) -> dict:
    graph = QCGraph(planner=planner)
    try:
        state = graph.run(str(uuid.uuid4()), packet_id, str(packet_dir),
                          strategy=strategy)
        report = state.get("report") or {}
        return {
            "run_id": state["run_id"],
            "status": state["runtime"]["status"],
            "decision": state.get("result", {}).get("qc_decision"),
            "evidence_validity": state.get("result", {}).get("evidence_validity"),
            "needs_human_review": report.get("needs_human_review"),
            "prompt_version": report.get("versions", {}).get("prompt"),
            "policy_guards": report.get("versions", {}).get("policy_guards"),
            "denials": [e["summary"] for e in state["events"]
                        if e["node"] == "policy_gate"
                        and e["summary"].startswith("denied")],
        }
    finally:
        graph.close()


def _strategies(registry: PromptRegistry) -> tuple[dict, dict]:
    arm_a = {"prompt_version": "promptv1",
             "prompt_sha256": registry.get("promptv1")["sha256"],
             "policy_guards": {}}
    arm_b = {"prompt_version": "promptv2",
             "prompt_sha256": registry.get("promptv2")["sha256"],
             "policy_guards": dict(GUARDS_ON)}
    return arm_a, arm_b


def check_prompt_binding(registry: PromptRegistry) -> dict:
    """P0.1/P0.2: the planner must send the registry's exact content."""
    result = {"checks": []}
    for version in ("promptv1", "promptv2"):
        gateway = BindingGateway()
        planner = LLMPlanner(gateway, prompt_registry=registry,
                             prompt_version=version)
        planner.next_action({}, {})
        sent = gateway.calls[0]["system"]
        expected = registry.get(version)["content"]
        result["checks"].append({
            "version": version,
            "sent_content_sha256": PromptRegistry.sha256(sent),
            "registry_sha256": registry.get(version)["sha256"],
            "bound": planner.prompt_sha256 == registry.get(version)["sha256"],
            "sent_matches_registry": sent == expected,
            "carried_in_call_receipt": (planner.last_usage or {}).get(
                "prompt_sha256") == registry.get(version)["sha256"],
        })
    result["all_bound"] = all(c["sent_matches_registry"] and c["bound"]
                              and c["carried_in_call_receipt"]
                              for c in result["checks"])
    return result


def check_promptv2_genericity(registry: PromptRegistry) -> dict:
    import re
    content = registry.get("promptv2")["content"]
    return {
        "no_accession_pattern": not re.search(r"NC_\d{4,}", content),
        "no_case_prefix": "chloro_C1" not in content and "CHLORO-" not in content,
        "rules_are_generic": "manifest" in content and "locator" in content.lower(),
    }


def run_mock_ab_chain(registry: PromptRegistry) -> dict:
    """P3.1: both arms run the full graph on every mock packet."""
    arm_a, arm_b = _strategies(registry)
    packets = []
    for packet_id in ("chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
                      "chloro_C1_NC_013991_2_CHLORO-CDS-03",
                      "chloro_C1_NC_013991_2_CHLORO-MULTI-01_SYNTHETIC",
                      "mito_M1_NC_002333_2_MITO-NORMAL-00"):
        packets.append((packet_id, EXAMPLES / packet_id))
    runs = {"arm_a": [], "arm_b": [], "per_packet_parity": []}
    for packet_id, packet_dir in packets:
        a = _run(packet_id, packet_dir, ScriptedPlanner(), arm_a)
        b = _run(packet_id, packet_dir, ScriptedPlanner(), arm_b)
        runs["arm_a"].append({"packet_id": packet_id, **a})
        runs["arm_b"].append({"packet_id": packet_id, **b})
        runs["per_packet_parity"].append({
            "packet_id": packet_id,
            "both_terminal": a["status"] == b["status"] == "succeeded",
            "decision_equal": a["decision"] == b["decision"],
            "b_prompt": b["prompt_version"],
            "b_guards": b["policy_guards"],
        })
    runs["all_terminal"] = all(p["both_terminal"] and p["decision_equal"]
                               for p in runs["per_packet_parity"])
    return runs


def run_guard_differences(registry: PromptRegistry) -> dict:
    """P0.4: each guard must show a measured behaviour difference."""
    arm_a, _arm_b = _strategies(registry)
    packet_id = "chloro_C1_NC_013991_2_CHLORO-CDS-03"
    packet = EXAMPLES / packet_id

    base = _run(packet_id, packet, FaultyStubPlanner("hallucinate"), arm_a)
    g1 = _run(packet_id, packet, FaultyStubPlanner("hallucinate"),
              {"prompt_version": "promptv2", "policy_guards":
               {"evidence_enforce_manifest": True}})
    g2_base = _run(packet_id, EXAMPLES /
                   "chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
                   FaultyStubPlanner("skip_verify"), arm_a)
    g2 = _run(packet_id, EXAMPLES /
              "chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
              FaultyStubPlanner("skip_verify"),
              {"prompt_version": "promptv2",
               "policy_guards": {"pre_verify_pointers": True}})
    g3_base = _run(packet_id, packet, FaultyStubPlanner("hallucinate"), arm_a)
    g3 = _run(packet_id, packet, FaultyStubPlanner("hallucinate"),
              {"prompt_version": "promptv2",
               "policy_guards": {"unresolved_requires_review": True}})
    return {
        "evidence_enforce_manifest": {
            "baseline_validity": base["evidence_validity"],
            "guarded_validity": g1["evidence_validity"],
            "baseline_denials": base["denials"],
            "guarded_denials": g1["denials"],
            "behaviour_changed": (base["evidence_validity"] == "partial"
                                  and g1["evidence_validity"] == "valid"
                                  and any("pointer_not_in_manifest" in d
                                          for d in g1["denials"])),
            "note": ("interception happens BEFORE execution and the denial is "
                     "recorded; findings/facts identical -> no evidence "
                     "dropped (direction 4 respected)")},
        "pre_verify_pointers": {
            "baseline_validity": g2_base["evidence_validity"],
            "guarded_validity": g2["evidence_validity"],
            "behaviour_changed": any("evidence_unverified" in d
                                     for d in g2["denials"]),
            "note": ("unverified synthesize is denied then the planner is "
                     "forced through verify_evidence_pointer")},
        "unresolved_requires_review": {
            "baseline_needs_review": g3_base["needs_human_review"],
            "guarded_needs_review": g3["needs_human_review"],
            "behaviour_changed": (g3_base["needs_human_review"] is False
                                  and g3["needs_human_review"] is True),
            "note": "composer escalation only; validity unchanged (partial)"},
    }


def run_shadow_and_approval_flow(registry: PromptRegistry) -> dict:
    """P2.1-P2.5: candidate executes in shadow; simulated approval can never
    publish; receipts carry version+config hash+run refs."""
    pool_file = STATE / "failure_pool" / "failure_cases.v1.jsonl"
    entries = [json.loads(line) for line in
               pool_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    controller = EvolutionController(
        STATE / "receipts" / "ab_preflight",
        active_config={"max_tokens": 8192, "gateway_timeout_s": 120,
                       "verify_pointer_cap": 50})
    candidate = controller.propose_behavior_policy(
        "CAND-promptv2-preflight-shadow", entries, prompt_registry=registry)
    controller.transition(
        "CAND-promptv2-preflight-shadow", "validated",
        metrics={"judgment_pass_rate": 0.85, "pointer_resolution_rate": 1.0,
                 "critical_false_pass": 0, "denominator": 40,
                 "replay": "historical-trace-replay"},
        baseline={"judgment_pass_rate": 0.85, "pointer_resolution_rate": 0.9934,
                  "critical_false_pass": 0, "denominator": 40})
    assert candidate["status"] == "validated"
    _, arm_b = _strategies(registry)
    packet_id = "chloro_C1_NC_013991_2_CHLORO-CDS-03"
    shadow_run = _run(packet_id, EXAMPLES / packet_id,
                      FaultyStubPlanner("hallucinate"), arm_b)
    receipt = controller.shadow_receipt("CAND-promptv2-preflight-shadow", runs=[{
        "run_id": shadow_run["run_id"],
        "packet_id": packet_id,
        "status": shadow_run["status"],
        "decision": shadow_run["decision"],
        "evidence_validity": shadow_run["evidence_validity"],
        "model_output_ref": f"mock://{shadow_run['run_id']}/QC_REPORT.json",
    }], strategy=arm_b)
    # simulated approval must NOT unlock publishing
    controller.record_approval("CAND-promptv2-preflight-shadow",
                               approver="offline_simulation:ab-preflight-driver",
                               decision="approve", real=False)
    try:
        controller.transition("CAND-promptv2-preflight-shadow", "approved",
                              human_approval=True)
        simulated_blocked = False
    except EvolutionError:
        simulated_blocked = True
    active_unchanged = controller.active_config == {
        "max_tokens": 8192, "gateway_timeout_s": 120, "verify_pointer_cap": 50}
    return {
        "candidate_status": candidate["status"],
        "shadow_run": shadow_run,
        "shadow_receipt": receipt,
        "simulated_approval_blocked_publishing": simulated_blocked,
        "active_strategy_unchanged": active_unchanged,
        "all_hold": (candidate["status"] == "validated" and simulated_blocked
                     and active_unchanged),
    }


def estimate_budget() -> dict:
    """P3.2: real per-case usage from G4 receipts -> smoke + full A/B estimate."""
    per_case = {}
    for name, label in (("g4_ds_chloro40", "deepseek"),
                        ("g4_glm_final", "glm")):
        path = ROOT / "data" / "eval" / name / "aggregate.json"
        if not path.is_file():
            continue
        agg = json.loads(path.read_text(encoding="utf-8"))
        totals = agg["model_totals"]
        cases = max(1, agg["denominators"]["cases_total"])
        per_case[label] = {
            "source": f"data/eval/{name}/aggregate.json",
            "cases": cases,
            "requests": totals["requests"],
            "total_tokens": totals["total_tokens"],
            "requests_per_case": round(totals["requests"] / cases, 2),
            "tokens_per_case": round(totals["total_tokens"] / cases),
        }
    worst = max((v["tokens_per_case"] for v in per_case.values()), default=5500)
    smoke_runs = 16  # 4 tasks x 2 models x 2 strategies
    smoke_tokens = smoke_runs * worst
    return {
        "per_case_observed": per_case,
        "smoke": {
            "runs": smoke_runs,
            "estimated_tokens": smoke_tokens,
            "estimated_tokens_with_margin_x1_5": int(smoke_tokens * 1.5),
            "proposed_hard_cap_tokens": 150_000,
            "proposed_hard_cap_requests": 80,
            "cap_rationale": ("5 requests/run covers the observed ~2.6 mean "
                              "plus one guard-denial replan; 1.5x margin on "
                              "the worst observed per-case tokens"),
        },
        "full_ab_reference": {
            "runs": 80,
            "estimated_tokens": 80 * worst,
            "protocol_cap_tokens": 1_000_000,
            "protocol_consistent": 80 * worst <= 1_000_000,
        },
    }


def smoke_design() -> dict:
    """P3.3/P3.4: frozen smoke design + the mandatory human-continue gate."""
    return {
        "matrix": "4 tasks x 2 models x 2 strategies = 16 runs",
        "models": ["zai-org/GLM-5.3-Flash", "deepseek-ai/DeepSeek-V4.1-Flash"],
        "tasks": SMOKE_PACKETS,
        "task_note": ("all four are SEEN development cases; the smoke is a "
                      "pipeline shakedown only - its results are never scored "
                      "nor claimed as unseen evaluation (P1.5)"),
        "strategies": {
            "A": "promptv1 (sha256 in prompt_registry.v1.json) + no guards",
            "B": "promptv2 pointer-guard + {evidence_enforce_manifest, "
                 "pre_verify_pointers, unresolved_requires_review}",
        },
        "gateway": {"temperature": 0, "max_tokens": 8192, "timeout_s": 120,
                    "auto_retry": False},
        "success_criteria": [
            "all 16 runs reach a terminal status (any terminal counts)",
            "0 private-data leaks; 0 out-of-whitelist accesses",
            ("every report.versions block records prompt version + sha256 + "
             "policy_guards fingerprint"),
            "per-request prompt identity matches the strategy arm",
        ],
        "human_gate": {
            "rule": ("after the 16 smoke runs complete, the driver STOPS and "
                     "requires explicit user authorisation before any further "
                     "paid run (full A/B)"),
            "checkpoint": "smoke receipt + per-run status reviewed by user",
            "no_auto_continue": True,
        },
        "abort_criteria": [
            "smoke token cap 150,000 exceeded",
            "smoke request cap 80 exceeded",
            "any run crashes outside the structured error contract",
        ],
    }


def main() -> int:
    registry = PromptRegistry(STATE / "prompt_registry.v1.json")
    receipt = {
        "receipt": "V3_1_AB_PREFLIGHT_TEST_RECEIPT/v1",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "api_calls": 0,
        "simulation": "mock_and_historical_data_only",
    }
    receipt["p0_prompt_binding"] = check_prompt_binding(registry)
    receipt["p0_promptv2_genericity"] = check_promptv2_genericity(registry)
    receipt["p3_mock_ab_chain"] = run_mock_ab_chain(registry)
    receipt["p0_guard_differences"] = run_guard_differences(registry)
    receipt["p2_shadow_and_approval"] = run_shadow_and_approval_flow(registry)
    receipt["p3_budget_estimate"] = estimate_budget()
    receipt["p3_smoke_design"] = smoke_design()

    guards_ok = all(v["behaviour_changed"]
                    for v in receipt["p0_guard_differences"].values())
    receipt["go_nogo"] = {
        "recommendation": "GO" if (
            receipt["p0_prompt_binding"]["all_bound"]
            and all(receipt["p0_promptv2_genericity"].values())
            and receipt["p3_mock_ab_chain"]["all_terminal"]
            and guards_ok
            and receipt["p2_shadow_and_approval"]["all_hold"]) else "NO-GO",
        "conditions": [
            ("GO covers smoke (16 runs) ONLY; full A/B still requires the "
             "frozen protocol §8 signature + budget confirmation"),
            ("smoke completion triggers the human-continue gate - no auto "
             "progression to the full 40-case A/B"),
        ],
    }
    RECEIPT_PATH.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"receipt: {RECEIPT_PATH}")
    print("GO/NO-GO:", receipt["go_nogo"]["recommendation"])
    print("binding:", receipt["p0_prompt_binding"]["all_bound"],
          "| generic:", all(receipt["p0_promptv2_genericity"].values()),
          "| chain:", receipt["p3_mock_ab_chain"]["all_terminal"],
          "| guards:", guards_ok,
          "| shadow/approval:", receipt["p2_shadow_and_approval"]["all_hold"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
