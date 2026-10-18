"""G5 offline evaluator (plan §9 / EVAL_PROTOCOL.md).

Two-phase, isolation-disciplined evaluation of V3 against the frozen V2
benchmark:

  Phase A (agent):    V3 graph runs on the PUBLIC view of each V2 candidate
                      case (task.md + artifacts/). private/ is never opened.
  Phase B (grader):   a subprocess under the V2 repository's own interpreter
                      imports the frozen grader and reads private gold; V3
                      code never does.
  Aggregate:          denominators and scores only - no private bytes.

Usage:
    python scripts/eval_offline.py --candidates <V2 candidates dir> \
        [--limit N] [--match SUBSTRING] [--out data/eval/<tag>]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent.adapters.v2_adapter import V2_ADAPTER_VERSION
from genomeqc_agent.eval.adapter import map_report
from genomeqc_agent.runtime.graph import QCGraph

DEFAULT_V2_PYTHON = Path(
    r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2\bench\inspect_adapter\.venv\Scripts\python.exe")
WORKER = Path(__file__).resolve().parent / "eval_grader_worker.py"


def discover_cases(candidates: Path, match: str, limit: int) -> list[Path]:
    cases = []
    for case_dir in sorted(candidates.iterdir()):
        if not case_dir.is_dir():
            continue
        if not (case_dir / "task.md").is_file() or not (case_dir / "artifacts").is_dir():
            continue
        if match and match not in case_dir.name:
            continue
        cases.append(case_dir)
        if len(cases) >= limit:
            break
    return cases


AUTHORIZED_PROVIDERS = {"glm", "deepseek"}  # user-authorized G4 providers


def build_planner(kind: str):
    """Scripted planner by default; LLM planner only for the authorized G4
    provider configured in .env (no silent fallbacks between providers)."""
    if kind == "scripted":
        from genomeqc_agent.model.scripted_planner import ScriptedPlanner
        return ScriptedPlanner()
    if kind == "llm":
        from genomeqc_agent import config
        from genomeqc_agent.model.gateway import ChatGateway
        from genomeqc_agent.model.llm_planner import LLMPlanner
        config.load_dotenv()
        env = config.model_env()
        if env["provider"] not in AUTHORIZED_PROVIDERS or not env["api_key"] \
                or not env["api_base"]:
            raise SystemExit(f"G4 LLM planner requires an authorized provider "
                             f"{sorted(AUTHORIZED_PROVIDERS)} with key/base in .env")
        # long reasoning spikes can exceed the 60s default; a longer client
        # timeout is a config choice, NOT an auto-retry of paid calls
        gateway = ChatGateway(env["api_key"], env["api_base"], env["model_id"],
                              timeout=120.0)
        return LLMPlanner(gateway)
    raise SystemExit(f"unknown planner kind: {kind}")


def run_eval(candidates: Path, out_dir: Path, limit: int, match: str,
             v2_python: Path, planner_kind: str = "scripted",
             planner_instance=None) -> dict:
    import uuid

    out_dir.mkdir(parents=True, exist_ok=True)
    cases = discover_cases(candidates, match, limit)
    planner = planner_instance or build_planner(planner_kind)
    graph = QCGraph(db_path=out_dir / "eval_checkpoint.sqlite3", planner=planner)
    records = []

    def write_aggregate() -> None:
        """Incremental snapshot: a crashed 40-case run keeps its receipts."""
        graded_now = [r for r in records if r.get("judgment") in ("pass", "fail")]
        snapshot = {
            "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "v2_adapter_version": V2_ADAPTER_VERSION,
            "planner": {"kind": planner_kind, "provider": planner.provider,
                        "model_id": planner.model_id},
            "candidates_root": str(candidates),
            "denominators": {
                "cases_total": len(records),
                "graded": len(graded_now),
                "skipped_phase_a": sum(1 for r in records if r.get("phase_a") == "no_report"),
                "skipped_not_mappable": sum(1 for r in records if r.get("mappable") is False),
                "phase_b_errors": sum(1 for r in records
                                      if str(r.get("phase_b", "")).startswith("error")),
            },
            "judgment_counts": {
                "pass": sum(1 for r in graded_now if r["judgment"] == "pass"),
                "fail": sum(1 for r in graded_now if r["judgment"] == "fail"),
            },
            "model_totals": {
                "requests": sum(r.get("model_requests", 0) for r in records),
                "prompt_tokens": sum(u.get("prompt_tokens") or 0
                                     for r in records for u in r.get("model_usage", [])),
                "completion_tokens": sum(u.get("completion_tokens") or 0
                                         for r in records for u in r.get("model_usage", [])),
                "total_tokens": sum(u.get("total_tokens") or 0
                                    for r in records for u in r.get("model_usage", [])),
                "calls_with_error": sum(1 for r in records for u in r.get("model_usage", [])
                                        if u.get("error")),
            },
            "records": records,
        }
        (out_dir / "aggregate.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return snapshot

    try:
        for case_dir in cases:
            record = {"case": case_dir.name}
            run_id = str(uuid.uuid4())
            state = graph.run(run_id, case_dir.name, str(case_dir),
                              max_tool_calls=32)
            report = state.get("report")
            runtime = state.get("runtime") or {}
            case_out = out_dir / case_dir.name
            agent_dir = case_out / "agent"
            agent_dir.mkdir(parents=True, exist_ok=True)
            record["model_usage"] = runtime.get("model_usage", [])
            record["model_requests"] = (state.get("budgets") or {}).get("model_requests", 0)
            if report is None:
                record.update(phase_a="no_report",
                              status=runtime.get("status"),
                              error=runtime.get("last_error"))
                records.append(record)
                write_aggregate()
                print(f"[{len(records)}/{len(cases)}] {case_dir.name}: "
                      f"phase_a no_report ({record.get('status')})", flush=True)
                continue
            (agent_dir / "QC_REPORT.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            mapped = map_report(report)
            record["mappable"] = mapped["mappable"]
            if not mapped["mappable"]:
                record["skip_reason"] = mapped["reason"]
                records.append(record)
                write_aggregate()
                print(f"[{len(records)}/{len(cases)}] {case_dir.name}: "
                      f"skipped ({record['skip_reason'][:80]})", flush=True)
                continue
            input_path = agent_dir / "v2_output.json"
            input_path.write_text(json.dumps(mapped["output"], ensure_ascii=False,
                                             indent=2) + "\n", encoding="utf-8")
            scorecard_path = case_out / "grader" / "scorecard.json"
            proc = subprocess.run(
                [str(v2_python), str(WORKER), "--case", str(case_dir),
                 "--input", str(input_path), "--out", str(scorecard_path)],
                capture_output=True, text=True, timeout=120, check=False)
            record["phase_b"] = "ok" if proc.returncode == 0 else f"error: {proc.stderr[-300:]}"
            if proc.returncode == 0:
                record["judgment"] = json.loads(proc.stdout.strip())["judgment"]
                record["scorecard"] = str(scorecard_path)
            records.append(record)
            write_aggregate()
            print(f"[{len(records)}/{len(cases)}] {case_dir.name}: "
                  f"judgment={record.get('judgment')}", flush=True)
    finally:
        graph.close()
    return write_aggregate()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--match", default="")
    parser.add_argument("--v2-python", default=str(DEFAULT_V2_PYTHON))
    parser.add_argument("--planner", default="scripted", choices=["scripted", "llm"])
    args = parser.parse_args(argv)

    candidates = Path(args.candidates)
    tag = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    out_dir = Path(args.out) if args.out else Path("data/eval") / tag
    aggregate = run_eval(candidates, out_dir, args.limit, args.match,
                         Path(args.v2_python), planner_kind=args.planner)
    print(json.dumps(aggregate["denominators"], ensure_ascii=False))
    print(json.dumps(aggregate["judgment_counts"], ensure_ascii=False))
    print(f"aggregate: {out_dir / 'aggregate.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
