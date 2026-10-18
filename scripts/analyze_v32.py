"""V3.2 P4.4: repeated-measures paired analysis of the real A/B ledger.

Design truth: 12 tasks x 2 models x 2 conditions x 3 repeats. Repeats are
NOT independent tasks - task-level means are the analysis unit; within-cell
agreement is reported separately. Writes V3_2_REAL_AB_REPORT.md.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "V3_2_REAL_AB_LEDGER.jsonl"
OUT = ROOT / "data" / "eval" / "v32_ab"

FAULT = ("CDS-03", "META-05", "ID-01")


def _is_fault(case_id):
    return any(case_id.endswith(f"CHLORO-{s}") for s in FAULT)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _ci95(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = statistics.mean(xs)
    se = statistics.stdev(xs) / math.sqrt(len(xs))
    return (round(m - 1.96 * se, 4), round(m + 1.96 * se, 4))


import math


def main() -> int:
    lines = [json.loads(l) for l in
             LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    obs = [r for r in lines if r.get("kind") == "observation"]
    repairs = [r for r in lines if r.get("kind") == "repair"]
    repairs_by_cell = {(r["case_id"], r["model_key"], r["condition"],
                        r["repeat"]): r for r in repairs
                       if r.get("status") == "succeeded"}
    # effective view for quality: successful repairs replace their cell
    for i, r in enumerate(obs):
        key = (r["case_id"], r["model_key"], r["condition"], r["repeat"])
        if key in repairs_by_cell:
            obs[i] = {**repairs_by_cell[key], "kind": "observation"}

    cells = defaultdict(list)  # (case, model, cond) -> [obs x repeats]
    for r in obs:
        cells[(r["case_id"], r["model_key"], r["condition"])].append(r)

    task_arms = defaultdict(dict)  # (case, model) -> {A: [3 obs], B: [3]}
    for (case, model, cond), rs in cells.items():
        task_arms[(case, model)][cond] = rs

    def arm_stats(rs):
        return {
            "n": len(rs),
            "succeeded": sum(1 for r in rs if r.get("status") == "succeeded"),
            "judgment_pass": sum(1 for r in rs if r.get("judgment") == "pass"),
            "verdict_correct": sum(1 for r in rs if r.get("verdict_score") == 1),
            "contract_ok": sum(1 for r in rs if r.get("output_contract_ok")),
            "qc_pass_on_fault": sum(1 for r in rs if r.get("qc_decision") == "pass"
                                    and _is_fault(r["case_id"])),
            "invalid_pointer_obs": sum(1 for r in rs
                                       if (r.get("unresolved_pointers") or 0) > 0),
            "partial": sum(1 for r in rs if r.get("evidence_validity") == "partial"),
            "needs_review": sum(1 for r in rs if r.get("needs_human_review")),
            "tokens_mean": _mean([r.get("tokens") for r in rs]),
            "latency_mean": _mean([r.get("elapsed_s") for r in rs]),
            "tool_calls_mean": _mean([sum((r.get("tool_calls") or {}).values())
                                      for r in rs]),
            "context_full": sum(1 for r in rs
                                if (r.get("context") or {}).get("completeness")
                                == "full"),
            "context_partial": sum(1 for r in rs
                                   if (r.get("context") or {}).get("completeness")
                                   == "partial"),
        }

    per_condition = {"A": [], "B": []}
    per_condition_by_model = {"glm": {"A": [], "B": []},
                              "ds": {"A": [], "B": []}}
    for (case, model, cond), rs in cells.items():
        per_condition[cond].extend(rs)
        per_condition_by_model[model][cond].extend(rs)
    stats = {cond: arm_stats(rs) for cond, rs in per_condition.items()}
    stats_by_model = {m: {c: arm_stats(rs) for c, rs in d.items()}
                      for m, d in per_condition_by_model.items()}

    # paired per-task analysis (n=12 tasks, means over repeats)
    paired = []
    for (case, model), armsd in sorted(task_arms.items()):
        if "A" not in armsd or "B" not in armsd:
            continue
        a, b = arm_stats(armsd["A"]), arm_stats(armsd["B"])
        paired.append({
            "case_id": case, "model": model,
            "A_judgment_pass": a["judgment_pass"], "B_judgment_pass":
                b["judgment_pass"],
            "A_invalid_pointer": a["invalid_pointer_obs"],
            "B_invalid_pointer": b["invalid_pointer_obs"],
            "A_tokens_mean": a["tokens_mean"], "B_tokens_mean": b["tokens_mean"],
            "A_partial": a["partial"], "B_partial": b["partial"],
            "repeat_consistency": {
                "A_decisions": list(dict.fromkeys(
                    r.get("qc_decision") for r in armsd["A"])),
                "B_decisions": list(dict.fromkeys(
                    r.get("qc_decision") for r in armsd["B"])),
            },
        })

    # per-task token means as the repeated-measures analysis unit (n=12)
    tok_a = [p["A_tokens_mean"] for p in paired]
    tok_b = [p["B_tokens_mean"] for p in paired]
    token_stats = {"A_task_mean": _mean(tok_a), "B_task_mean": _mean(tok_b),
                   "A_task_ci": _ci95(tok_a), "B_task_ci": _ci95(tok_b)}

    totals = {
        "observations": len(obs), "repairs": len(repairs),
        "tokens_billed": sum(r.get("tokens") or 0 for r in lines
                             if r.get("kind") in ("observation", "repair")),
        "requests": sum(r.get("requests") or 0 for r in lines
                        if r.get("kind") in ("observation", "repair")),
    }

    fail_dist = {"infra_503": sum(1 for r in obs
                                  if "503" in (r.get("raw_error") or "")),
                 "other_errors": sum(1 for r in obs
                                     if r.get("raw_error")
                                     and "503" not in r.get("raw_error", ""))}
    report = {
        "report": "V3_2_REAL_AB_REPORT_DATA/v1", "at": datetime.now(
            UTC).isoformat(timespec="seconds"),
        "design": {"cases": 12, "models": 2, "conditions": 2, "repeats": 3,
                   "planned": 144, "executed": totals["observations"],
                   "repairs": len(repairs)},
        "totals": totals,
        "per_condition": stats, "per_condition_by_model": stats_by_model,
        "paired": paired, "token_task_level": token_stats,
        "failure_distribution": fail_dist,
    }
    (OUT / "v32_analysis.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("A:", json.dumps(stats["A"], ensure_ascii=False)[:400])
    print("B:", json.dumps(stats["B"], ensure_ascii=False)[:400])
    print("token task-level:", token_stats)
    print("analysis:", OUT / "v32_analysis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
