"""Cross-process resume worker: resume one run from a checkpoint file.

Usage (process B of the cross-process recovery demo):
    python scripts/resume_worker.py --db <checkpoint.sqlite3> --run-id <uuid>
        [--interrupt-before plan_next_action] [--out <runs dir>]

Prints a JSON summary (status/decision/facts_ok) on success.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent.runtime.graph import QCGraph


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--interrupt-before", default=None,
                        help="comma-separated node names (must match process A)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    interrupts = args.interrupt_before.split(",") if args.interrupt_before else None
    graph = QCGraph(db_path=args.db, out_dir=args.out,
                    interrupt_before=interrupts)
    try:
        state = graph.resume(args.run_id)
    finally:
        graph.close()
    print(json.dumps({
        "status": state["runtime"]["status"],
        "decision": (state.get("result") or {}).get("qc_decision"),
        "facts_ok": state.get("facts") is not None,
        "error": (state["runtime"].get("last_error") or "")[:300],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
