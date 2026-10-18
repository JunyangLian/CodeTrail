"""Isolated grader worker (G5 Phase B).

Runs under the V2 repository's own interpreter - NOT the V3 runtime venv.
This is the ONLY process in V3 that may import the frozen V2 grader and read
private/expected.json. Its output is a scorecard; private gold never flows
back into the V3 package, database, or reports.

    <V2 venv python> scripts/eval_grader_worker.py \
        --case <candidate dir> --input <v2_output.json> --out <scorecard.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_V2_REPO = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="V2 candidate case dir")
    parser.add_argument("--input", required=True, help="mapped v2_output.json")
    parser.add_argument("--out", required=True, help="scorecard output path")
    parser.add_argument("--v2-repo", default=str(DEFAULT_V2_REPO))
    args = parser.parse_args(argv)

    repo = Path(args.v2_repo).resolve()
    # V2's dual sys.path coupling: repo root (bench.*) + bench/ (top-level
    # modules such as visible_input imported by harness_common).
    sys.path[:0] = [str(repo), str(repo / "bench")]
    from bench.organelle_v2.rubric.deterministic_grader import grade_output

    case_dir = Path(args.case)
    expected = json.loads((case_dir / "private" / "expected.json").read_text(encoding="utf-8"))
    output_text = Path(args.input).read_text(encoding="utf-8")
    scorecard = grade_output(output_text, expected, case_dir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    # stdout: verdict-free summary only (safe for logs)
    print(json.dumps({"judgment": scorecard["judgment"],
                      "verdict_score": scorecard["verdict"]["score"],
                      "output_contract_ok": scorecard["output_contract"]["score"] == 1}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
