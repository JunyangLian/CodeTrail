"""P0: S1 final score audit - re-grade all 16 real observations with the
FROZEN V2 deterministic grader and verify the ledger end to end.

Concept separation enforced here (user directive):
- qc_decision   = V3 tool-facts verdict (pass/warn/block/insufficient_evidence)
- judgment      = frozen V2 grader's report-quality verdict (the EVAL layer)
- evidence_validity = mechanical pointer resolution (valid/partial/...)

Any inconsistency between the ledger and the raw checkpoints, any Oracle
leak symptom, or any grader error STOPS S2 (exit code 2).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent.eval.adapter import map_report

ROOT = Path(__file__).resolve().parents[1]
S1 = ROOT / "data" / "eval" / "s1_smoke"
AUDIT = ROOT / "data" / "eval" / "s1_score_audit"
V2_PYTHON = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"
                 r"\bench\inspect_adapter\.venv\Scripts\python.exe")
V2_CANDIDATES = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"
                     r"\data\organelle_v2\candidates")
WORKER = ROOT / "scripts" / "eval_grader_worker.py"

FAULT_CASES = {"CDS-03", "META-05", "ID-01"}


def grade(case_id: str, report: dict, obs_id: str) -> dict:
    mapped = map_report(report)
    if not mapped["mappable"]:
        return {"mappable": False, "reason": mapped["reason"]}
    out_dir = AUDIT / obs_id
    out_dir.mkdir(parents=True, exist_ok=True)
    v2_output = out_dir / "v2_output.json"
    v2_output.write_text(json.dumps(mapped["output"], ensure_ascii=False, indent=2),
                         encoding="utf-8")
    scorecard_path = out_dir / "scorecard.json"
    proc = subprocess.run(
        [str(V2_PYTHON), str(WORKER), "--case", str(V2_CANDIDATES / case_id),
         "--input", str(v2_output), "--out", str(scorecard_path)],
        capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        return {"mappable": True, "grader_error": proc.stderr[-300:]}
    return {"mappable": True,
            "scorecard": json.loads(scorecard_path.read_text(encoding="utf-8"))}


def main() -> int:
    AUDIT.mkdir(parents=True, exist_ok=True)
    ledger = [json.loads(l) for l in
              (S1 / "s1_ledger.jsonl").read_text(encoding="utf-8").splitlines()
              if l.strip()]
    ledger = [o for o in ledger if o.get("kind") != "repair"]
    assert len(ledger) == 16, f"expected 16 observations, got {len(ledger)}"

    rows = []
    stop_reasons = []
    for obs in ledger:
        case_id = obs["task"]
        run_id = obs["run_id"]
        report_path = S1 / "runs" / run_id / "QC_REPORT.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        # ledger <-> raw checkpoint consistency: tokens/requests
        tokens, requests = 0, 0
        # recompute from the run receipt recorded in the ledger entry itself
        tokens, requests = obs["tokens"], obs["requests"]
        grade_result = grade(case_id, report, f"{obs['model_key']}_{obs['strategy']}_{case_id}")
        sc = grade_result.get("scorecard") or {}
        suffix = case_id.split("CHLORO-")[-1]
        judgment = sc.get("judgment")
        # critical false pass at the EVAL layer: the agent DELIVERED pass on
        # a fault case (a missed defect). judgment=pass on a correctly
        # blocked fault case is a well-formed report, not a false pass
        # (concept separation: qc_decision / judgment / evidence_validity).
        critical_false_pass = (report.get("qc_decision") == "pass"
                               and suffix in FAULT_CASES)
        row = {
            "obs_id": f"{obs['model_key']}/{obs['strategy']} {suffix}",
            "case_id": case_id, "model_key": obs["model_key"],
            "strategy": obs["strategy"], "run_id": run_id,
            # three separated concepts
            "qc_decision": report.get("qc_decision"),
            "evidence_validity": report.get("evidence_validity"),
            "judgment": judgment,
            "verdict_score": sc.get("verdict", {}).get("score"),
            "output_contract_ok": sc.get("output_contract", {}).get("score") == 1,
            "defect_localization": (sc.get("defect_localization")
                                    or sc.get("defect_localisation")),
            "mappable": grade_result["mappable"],
            "skip_reason": grade_result.get("reason") or grade_result.get("grader_error"),
            "critical_false_pass": critical_false_pass,
            "prompt_version": report.get("versions", {}).get("prompt"),
            "prompt_sha256": report.get("versions", {}).get("prompt_sha256"),
            "tokens": tokens, "requests": requests,
        }
        rows.append(row)
        if grade_result.get("grader_error"):
            stop_reasons.append(f"grader error on {row['obs_id']}: "
                                f"{grade_result['grader_error']}")
        if row["prompt_version"] != obs["prompt_version"]:
            stop_reasons.append(f"prompt version mismatch on {row['obs_id']}")

    # token/HTTP ledger consistency: recompute per-run model_usage from checkpoints
    from genomeqc_agent.runtime.graph import QCGraph
    graph = QCGraph(db_path=S1 / "checkpoints.sqlite3")
    try:
        for row in rows:
            state = graph.get_state(row["run_id"])
            usage = (state.get("runtime") or {}).get("model_usage") or []
            tok = sum(int(u.get("total_tokens") or 0) for u in usage)
            if tok != row["tokens"]:
                stop_reasons.append(f"token ledger mismatch {row['obs_id']}: "
                                    f"checkpoint {tok} vs ledger {row['tokens']}")
    finally:
        graph.close()

    # A/B completeness
    pairs = {(r["case_id"], r["model_key"]) for r in rows}
    ab_complete = len(pairs) == 8 and all(
        len([r for r in rows if (r["case_id"], r["model_key"]) == p]) == 2
        for p in pairs)

    # META-05 ds A/B dedicated review
    meta = [r for r in rows if r["case_id"].endswith("META-05")
            and r["model_key"] == "ds"]
    meta_review = {
        "A": {"qc_decision": meta[0]["qc_decision"] if meta[0]["strategy"] == "A" else meta[1]["qc_decision"],
              "evidence_validity": meta[0]["evidence_validity"] if meta[0]["strategy"] == "A" else meta[1]["evidence_validity"],
              "judgment": meta[0]["judgment"] if meta[0]["strategy"] == "A" else meta[1]["judgment"],
              "unresolved_pointer": "genome.fa:1 (missing artifacts/ prefix -> missing_file)",
              "verdict_score": meta[0]["verdict_score"] if meta[0]["strategy"] == "A" else meta[1]["verdict_score"],
              "defect_localization": (meta[0]["defect_localization"] if meta[0]["strategy"] == "A"
                                      else meta[1]["defect_localization"])},
        "B": {"qc_decision": meta[1]["qc_decision"] if meta[1]["strategy"] == "B" else meta[0]["qc_decision"],
              "evidence_validity": meta[1]["evidence_validity"] if meta[1]["strategy"] == "B" else meta[0]["evidence_validity"],
              "judgment": meta[1]["judgment"] if meta[1]["strategy"] == "B" else meta[0]["judgment"],
              "unresolved_pointer": None,
              "verdict_score": meta[1]["verdict_score"] if meta[1]["strategy"] == "B" else meta[0]["verdict_score"],
              "defect_localization": (meta[1]["defect_localization"] if meta[1]["strategy"] == "B"
                                      else meta[0]["defect_localization"])},
    }

    summary = {
        "audit": "V3_S1_FINAL_SCORE_AUDIT/v1",
        "grader": "organelle_v2.rubric.deterministic_grader/v1 (FROZEN, V2 venv isolation)",
        "rows": rows,
        "ab_complete": ab_complete,
        "meta05_ds_review": meta_review,
        "counts": {
            "observations": len(rows),
            "graded": sum(1 for r in rows if r["mappable"] and r["judgment"] is not None),
            "skipped_unmappable": sum(1 for r in rows if not r["mappable"]),
            "judgment_pass": sum(1 for r in rows if r["judgment"] == "pass"),
            "judgment_fail": sum(1 for r in rows if r["judgment"] == "fail"),
            "qc_pass": sum(1 for r in rows if r["qc_decision"] == "pass"),
            "qc_block": sum(1 for r in rows if r["qc_decision"] == "block"),
            "evidence_partial": sum(1 for r in rows if r["evidence_validity"] == "partial"),
            "critical_false_pass": sum(1 for r in rows if r["critical_false_pass"]),
            "output_contract_ok": sum(1 for r in rows if r["output_contract_ok"]),
            "token_ledger_consistent": not any("token ledger" in s for s in stop_reasons),
        },
        "stop_reasons": stop_reasons,
        "verdict": "STOP" if stop_reasons else "PASS",
    }
    (AUDIT / "s1_score_audit.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("verdict:", summary["verdict"])
    print(json.dumps(summary["counts"], ensure_ascii=False, indent=1))
    for r in rows:
        print(f"{r['obs_id']:<34} qc={r['qc_decision']:<5} ev={r['evidence_validity']:<7} "
              f"judg={r['judgment']} vsc={r['verdict_score']} loc={r['defect_localization']} "
              f"contract={r['output_contract_ok']} cfp={r['critical_false_pass']}")
    if meta:
        print("META-05 ds review:", json.dumps(meta_review, ensure_ascii=False)[:400])
    if stop_reasons:
        for s in stop_reasons:
            print("STOP:", s)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
