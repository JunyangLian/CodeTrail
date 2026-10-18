"""Offline smoke CLI: public packet -> tool facts -> QC report (JSON + MD).

Usage:
    python scripts/smoke_offline.py --packet examples/public_packets/<name> [--out data/runs]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent.jobs.smoke_pipeline import run_smoke


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", required=True, help="packet directory (task.md + artifacts/)")
    parser.add_argument("--out", default="data/runs", help="run output root")
    parser.add_argument("--domain", default="auto_detect_supported",
                        choices=["vertebrate_mitochondrion", "chloroplast",
                                 "auto_detect_supported"])
    args = parser.parse_args(argv)

    outcome = run_smoke(args.packet, domain=args.domain, out_dir=args.out)
    report = outcome["report"]
    print(f"run_id          : {report['run_id']}")
    print(f"packet          : {report['packet']['packet_id']} "
          f"(sha256 {report['packet']['input_sha256'][:12]}...)")
    print(f"qc_decision     : {report['qc_decision']}")
    print(f"evidence        : {report['evidence_validity']}")
    for finding in report["findings"]:
        marker = {"pass": "[PASS]", "block": "[BLOCK]", "warn": "[WARN]",
                  "insufficient_evidence": "[INSUF]"}[finding["status"]]
        print(f"  {marker} {finding['check_id']}: {finding['summary']}")
    print(f"model requests  : {outcome['model_requests']} (fake provider, offline)")
    print(f"tool calls      : {outcome['receipt']['tool_calls']}")
    if outcome["written"]:
        print("written:")
        for label, path in outcome["written"].items():
            print(f"  {label}: {path}")
    return 0 if report["evidence_validity"] != "invalid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
