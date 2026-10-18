"""One-off S2 repair pass (frozen protocol): bounded single rerun of the
infra-failed observation. The original failure stays in the ledger; the
repair is recorded separately as kind="repair" and never overwrites it."""
from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent import config
from genomeqc_agent.evolution.prompts import PromptRegistry
from genomeqc_agent.model.gateway import ChatGateway
from genomeqc_agent.model.llm_planner import LLMPlanner
from genomeqc_agent.runtime.graph import QCGraph

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "s2_experiment"
LEDGER = ROOT / "V3_S2_RUN_LEDGER.jsonl"
GUARDS_B = {"evidence_enforce_manifest": True, "pre_verify_pointers": True,
            "unresolved_requires_review": True}


def main() -> int:
    import os
    if os.environ.get("GENOMEQC_PAID_AUTHORIZED") != "true":
        raise SystemExit("BLOCKED: caller must set GENOMEQC_PAID_AUTHORIZED=true")
    config.load_dotenv()
    rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    obs = [r for r in rows if r.get("kind") == "observation"]
    failed = [r for r in obs if r.get("status") == "failed"]
    already = {r.get("repairs_original") for r in rows
               if r.get("kind") == "repair"}
    todo = [r for r in failed if r["run_id"] not in already]
    print(f"repair candidates: {len(todo)}")
    registry = PromptRegistry(ROOT / "evolution_state" / "prompt_registry.v1.json")
    gateway = ChatGateway(api_key=config.model_env()["api_key"],
                          api_base=config.model_env()["api_base"],
                          model_id="zai-org/GLM-5.3-Flash", timeout=120.0)
    from pathlib import Path as _P
    v2c = _P(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"
             r"\data\organelle_v2\candidates")
    for r in todo:
        strategy = {
            "prompt_version": r["prompt_version"],
            "prompt_sha256": r["prompt_sha256"],
            "policy_guards": dict(GUARDS_B) if r["arm"] == "glm_B" else {},
        }
        planner = LLMPlanner(gateway, max_tokens=8192, prompt_registry=registry,
                             prompt_version=r["prompt_version"])
        run_id = str(uuid.uuid4())
        graph = QCGraph(db_path=OUT / "checkpoints.sqlite3",
                        out_dir=OUT / "runs", planner=planner)
        try:
            started = __import__("time").monotonic()
            state = graph.run(run_id, r["case_id"], str(v2c / r["case_id"]),
                              max_model_requests=5, strategy=strategy)
            elapsed = round(__import__("time").monotonic() - started, 1)
        finally:
            graph.close()
        usage = (state.get("runtime") or {}).get("model_usage") or []
        tokens = sum(int(u.get("total_tokens") or 0) for u in usage)
        runtime = state.get("runtime") or {}
        result = state.get("result") or {}
        report = state.get("report") or {}
        repair = {
            "kind": "repair", "case_id": r["case_id"], "arm": r["arm"],
            "model_id": r["model_id"],
            "source_group": r.get("source_group"), "at": datetime.now(
                UTC).isoformat(timespec="seconds"),
            "run_id": run_id, "repairs_original": r["run_id"],
            "status": runtime.get("status"),
            "qc_decision": result.get("qc_decision"),
            "evidence_validity": result.get("evidence_validity"),
            "needs_human_review": report.get("needs_human_review"),
            "unresolved_pointers": len((state.get("evidence") or {})
                                       .get("unresolved", [])),
            "prompt_version": strategy["prompt_version"],
            "prompt_sha256": strategy["prompt_sha256"],
            "tokens": tokens, "requests": len(usage), "elapsed_s": elapsed,
            "raw_error": (runtime.get("last_error") or "")[:500],
            "note": "frozen-protocol bounded repair; original failure preserved",
        }
        if repair["status"] == "succeeded":
            rep = json.loads((OUT / "runs" / run_id / "QC_REPORT.json")
                             .read_text(encoding="utf-8"))
            sys.path.insert(0, str(ROOT / "scripts"))
            from run_s2_experiment import _grade
            repair.update(_grade(r["case_id"], rep,
                                 f"{r['case_id']}:{r['arm']}::repair"))
        with LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(repair, ensure_ascii=False) + "\n")
        print(f"repair {r['case_id']}:{r['arm']} -> {repair['status']} "
              f"dec={repair['qc_decision']} tok={tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
