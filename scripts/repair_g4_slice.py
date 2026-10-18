"""G4 repair round: re-run the infra-failed cases from the 40-case slice.

Follows the V2 repair-mode precedent: infra-failed observation slots are
re-run as a SEPARATE receipt; the original failures stay untouched in the
original aggregate. max_tokens raised 4096->8192 for this round only (one
first-round case exhausted 4096 on reasoning+content - recorded in
G4_GLM_RECEIPT.md). This is a config change for the new round, not a retry
of any paid call.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval_offline import run_eval

from genomeqc_agent import config
from genomeqc_agent.adapters.v2_adapter import V2_ADAPTER_VERSION
from genomeqc_agent.model.gateway import ChatGateway
from genomeqc_agent.model.llm_planner import LLMPlanner

CANDIDATES = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2\data\organelle_v2\candidates")
V2_PYTHON = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2\bench\inspect_adapter"
                 r"\.venv\Scripts\python.exe")
OUT = Path("data/eval/g4_glm_chloro40_repair")

FAILED_CASES = [
    "chloro_C1_NC_088768_1_CHLORO-ID-01",
    "chloro_C1_NC_088771_1_CHLORO-META-05",
    "chloro_C1_NC_140640_1_CHLORO-CDS-03",
]


def main() -> int:
    config.load_dotenv()
    env = config.model_env()
    gateway = ChatGateway(env["api_key"], env["api_base"], env["model_id"], timeout=120.0)
    planner = LLMPlanner(gateway, max_tokens=8192)

    records = []
    for case_name in FAILED_CASES:
        case_out = OUT / case_name
        aggregate = run_eval(CANDIDATES, case_out, limit=1, match=case_name,
                             v2_python=V2_PYTHON, planner_kind="llm",
                             planner_instance=planner)
        record = dict(aggregate["records"][0])
        record["case"] = case_name
        records.append(record)
        print(f"repair {case_name}: {record.get('judgment') or record.get('status')}",
              flush=True)
    gateway.close()

    graded = [r for r in records if r.get("judgment") in ("pass", "fail")]
    receipt = {
        "round": "repair",
        "repaired_cases": len(FAILED_CASES),
        "max_tokens": 8192,
        "v2_adapter_version": V2_ADAPTER_VERSION,
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "denominators": {
            "cases_total": len(records),
            "graded": len(graded),
            "still_failed": sum(1 for r in records if r.get("phase_a") == "no_report"),
        },
        "judgment_counts": {
            "pass": sum(1 for r in graded if r["judgment"] == "pass"),
            "fail": sum(1 for r in graded if r["judgment"] == "fail"),
        },
        "model_totals": {
            "requests": sum(r.get("model_requests", 0) for r in records),
            "total_tokens": sum(u.get("total_tokens") or 0
                                for r in records for u in r.get("model_usage", [])),
        },
        "records": records,
    }
    (OUT / "repair_aggregate.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt["denominators"], ensure_ascii=False))
    print(json.dumps(receipt["judgment_counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
