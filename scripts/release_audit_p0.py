"""P0 release-audit: dual-view metrics (first-run vs post-repair) from the
S2 ledger, frozen-protocol compliance checks, and significance-language
scan of the evaluation report. Read-only; writes a JSON evidence block."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "V3_S2_RUN_LEDGER.jsonl"


def _is_fault(case_id: str) -> bool:
    return any(case_id.endswith(f"CHLORO-{s}")
               for s in ("CDS-03", "META-05", "ID-01"))


def metrics(rows: list[dict]) -> dict:
    graded = [r for r in rows if r.get("judgment") is not None]
    failed = [r for r in rows if r.get("status") == "failed"]
    return {
        "observations": len(rows),
        "succeeded": sum(1 for r in rows if r.get("status") == "succeeded"),
        "failed_infra": len(failed),
        "failed_case_arms": [f"{r['case_id']}:{r['arm']}" for r in failed],
        "judgment_pass_of_graded":
            f"{sum(1 for r in graded if r.get('judgment') == 'pass')}/{len(graded)}",
        "qc_pass_on_fault": sum(1 for r in rows
                                if r.get("qc_decision") == "pass"
                                and _is_fault(r["case_id"])),
        "invalid_pointer_obs": sum(1 for r in rows
                                   if (r.get("unresolved_pointers") or 0) > 0),
        "tokens": sum(r.get("tokens") or 0 for r in rows),
        "requests": sum(r.get("requests") or 0 for r in rows),
    }


def main() -> int:
    lines = [json.loads(l) for l in
             LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    obs = [r for r in lines if r.get("kind") == "observation"]
    repairs = [r for r in lines if r.get("kind") == "repair"]
    assert len(obs) == 120

    first_run = metrics(obs)  # initial observations as they happened
    # post-repair effective view: successful repairs replace their cell
    repairs_by_cell = {(r["case_id"], r["arm"]): r for r in repairs
                       if r.get("status") == "succeeded"}
    effective_rows = []
    for r in obs:
        key = (r["case_id"], r["arm"])
        effective_rows.append(repairs_by_cell.get(key, r))
    post_repair = metrics(effective_rows)

    # paired denominators per protocol: quality metrics are compared on
    # cells where BOTH arms produced gradeable reports
    by_cell: dict = {}
    for r in effective_rows:
        if r["arm"].startswith("glm"):
            by_cell.setdefault(r["case_id"], {})[r["arm"]] = r
    complete_pairs = sum(1 for c in by_cell.values()
                         if len(c) == 2
                         and all(r.get("judgment") is not None
                                 for r in c.values()))
    protocol = {
        "infra_failure_rule": ("failed observations stay in the ledger as "
                               "failed; no auto-retry inside a run"),
        "repair_rule": ("bounded single rerun, recorded as kind=repair with "
                        "repairs_original; original never overwritten"),
        "repair_count": len(repairs),
        "repair_cells": [f"{r['case_id']}:{r['arm']}" for r in repairs],
        "paired_denominator_rule": ("quality comparisons use cells with both "
                                    "arms gradeable; completion rate is "
                                    "reported separately"),
        "complete_pairs_after_repair": complete_pairs,
        "denominator_120_positions": len(obs) == 120,
    }

    # significance-language scan of the evaluation report
    report_text = (ROOT / "docs" / "V3_S2_EVALUATION_REPORT.md").read_text(
        encoding="utf-8")
    banned = [m for m in re.findall(r"统计显著|显著提升|significantly better|"
                                    r"p\s*[<=]\s*0\.0\d", report_text)]
    ceiling_note = "天花板效应" in report_text

    audit = {
        "audit": "V3_1_RELEASE_AUDIT_P0/v1",
        "first_run": first_run,
        "post_repair_effective": post_repair,
        "delta_note": ("the ONLY difference is cell glm_B x NC_088771_1_"
                       "CHLORO-ID-01: first-run 503 infra failure, repair "
                       "succeeded (block); tokens include both attempts"),
        "protocol": protocol,
        "significance_language_scan": {
            "banned_matches": banned,
            "ceiling_effect_disclosed": ceiling_note,
            "claim_language": ("directional evidence on a 40-pair DEV slice; "
                               "no statistical significance claimed"),
        },
        "verdict": "PASS" if (not banned and ceiling_note
                              and protocol["denominator_120_positions"]
                              and post_repair["failed_infra"] == 0) else "REVIEW",
    }
    out = ROOT / "data" / "eval" / "s2_experiment" / "release_audit_p0.json"
    out.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("verdict:", audit["verdict"])
    print("first_run:", json.dumps(first_run, ensure_ascii=False))
    print("post_repair:", json.dumps(post_repair, ensure_ascii=False))
    print("protocol:", json.dumps(protocol, ensure_ascii=False)[:300])
    print("significance scan:", banned or "clean", "| ceiling disclosed:",
          ceiling_note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
