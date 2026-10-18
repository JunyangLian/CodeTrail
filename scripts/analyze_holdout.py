"""Final-round P3.5: independent holdout analysis (frozen metric list).

Reads data/eval/final_round/holdout_run/holdout_ledger.jsonl + scorecards +
expected.json labels. Every launched observation stays in the denominator;
repair rows supersede their original for effectives but both are reported.

Output: data/eval/final_round/holdout_run/holdout_metrics.json
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "final_round" / "holdout_run"
LEDGER = OUT / "holdout_ledger.jsonl"
MANIFEST = ROOT / "data" / "eval" / "final_round" / "holdout_dataset" / \
    "DATASET_MANIFEST.json"


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def main() -> int:
    rows = load(LEDGER)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    task_meta = {t["task_id"]: t for t in manifest["tasks"]}
    obs = [r for r in rows if r.get("kind") == "observation"]
    repairs = {r.get("repairs_original"): r for r in rows
               if r.get("kind") == "repair"}

    # effective row per cell: repair supersedes original when present
    eff = {}
    for r in obs:
        key = (r["case_id"], r["model_key"], r["condition"], r["repeat"])
        eff[key] = repairs.get(r["run_id"], r)
        eff[key]["_original_status"] = r["status"]

    def scorecard_for(rec: dict) -> dict | None:
        cell = (f"{rec['case_id']}__{rec['model_key']}_{rec['condition']}"
                f"_r{rec['repeat']}")
        p = OUT / "grading" / cell / "scorecard.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def classify(rec: dict) -> dict:
        sc = scorecard_for(rec)
        case = task_meta.get(rec["case_id"], {})
        is_fault = case.get("task_type") == "fault"
        status = rec.get("status")
        qc = rec.get("qc_decision")
        out = {
            "status": status,
            "infra_failure": status == "failed" and not rec.get("tokens"),
            "launched": True,
            "verdict_correct": None, "judgment_pass": None,
            "contract_ok": None, "dl_score": None,
            "invalid_pointer": 1 if (rec.get("unresolved_pointers") or 0) > 0
            else 0,
            "critical_false_pass": 1 if (is_fault and qc == "pass") else 0,
            "qc_decision": qc,
            "tokens": rec.get("tokens") or 0,
            "requests": rec.get("requests") or 0,
            "latency_s": rec.get("elapsed_s"),
            "mappable": None,
            "unmapped_reason": None,
        }
        if rec.get("mappable") is False:
            out["mappable"] = False
            out["unmapped_reason"] = rec.get("skip_reason")
            out["contract_ok"] = 0
            return out
        if sc is None:
            return out
        out["mappable"] = True
        out["verdict_correct"] = 1 if (sc.get("verdict") or {}).get("score") == 1 \
            else 0
        out["judgment_pass"] = 1 if sc.get("judgment") == "pass" else 0
        out["contract_ok"] = 1 if (sc.get("output_contract") or {}).get("score") \
            == 1 else 0
        dl = (sc.get("defect_localization") or {}).get("score")
        out["dl_score"] = dl
        return out

    classified = {k: classify(v) for k, v in eff.items()}

    def agg(selector, keys=None):
        vals = [c for c in classified.values() if selector(c)]
        return vals, len(vals)

    def rate(values: list, field: str) -> dict:
        xs = [v[field] for v in values if v[field] is not None]
        n_all = len(values)
        if not xs:
            return {"n_scored": 0, "n_total": n_all, "rate": None}
        return {"n_scored": len(xs), "n_total": n_all,
                "rate": round(sum(xs) / len(xs), 4)}

    summary: dict = {
        "plan": "V3_2_HOLDOUT_PLAN/v1",
        "observations_launched": len(obs),
        "observations_effective": len(eff),
        "repair_rows": len(repairs),
        "denominator_note": "all launched observations reported; repair "
                            "supersedes original per cell, originals kept",
    }

    for cond in ("A", "B"):
        vals = [c for (case, m, c2, r), c in classified.items() if c2 == cond]
        summary[cond] = {
            "n": len(vals),
            "status_succeeded": sum(1 for v in vals if v["status"] == "succeeded"),
            "infra_failures": sum(1 for v in vals if v["infra_failure"]),
            "qc_verdict_correct": rate(vals, "verdict_correct"),
            "output_contract_ok": rate(vals, "contract_ok"),
            "judgment_pass": rate(vals, "judgment_pass"),
            "invalid_pointer_rate": rate(vals, "invalid_pointer"),
            "critical_false_pass": sum(v["critical_false_pass"] for v in vals),
            "unmappable_reports": sum(1 for v in vals if v["mappable"] is False),
            "tokens_mean": round(statistics.mean(v["tokens"] for v in vals), 1),
            "latency_mean_s": round(statistics.mean(
                v["latency_s"] for v in vals if v["latency_s"] is not None), 1),
            "requests_mean": round(statistics.mean(
                v["requests"] for v in vals), 2),
            "context_completeness": sorted({(eff[k].get("context") or {})
                                            .get("completeness")
                                            for k, c in classified.items()
                                            if k[2] == cond
                                            and eff[k].get("context")}),
        }
    # paired (same case+model) A vs B
    pairs = {}
    for (case, m, cond, _r), c in classified.items():
        pairs.setdefault((case, m), {})[cond] = c
    per_model: dict[str, dict] = {}
    per_group: dict[str, dict] = {}
    group_of = {t["task_id"]: t["source_accession_version"]
                for t in manifest["tasks"]}
    for (case, m), pair in pairs.items():
        for level, key in (("model", m), ("group", group_of.get(case, "?"))):
            bucket = per_model if level == "model" else per_group
            b = bucket.setdefault(key, {"A": [], "B": []})
            b.setdefault("A", [])
            b.setdefault("B", [])
            for cond in ("A", "B"):
                if cond in pair:
                    b[cond].append(pair[cond])
    for name, bucket in (("by_model", per_model), ("by_source_group", per_group)):
        summary[name] = {}
        for key, conds in bucket.items():
            summary[name][key] = {}
            for cond in ("A", "B"):
                vals = conds.get(cond, [])
                summary[name][key][cond] = {
                    "n": len(vals),
                    "verdict_correct": rate(vals, "verdict_correct"),
                    "invalid_pointer": sum(v["invalid_pointer"] for v in vals),
                    "tokens_mean": round(statistics.mean(
                        (v["tokens"] for v in vals)), 0) if vals else None,
                }
    # fault-case localization (B arm + A arm, scored cases only)
    for cond in ("A", "B"):
        vals = [c for (case, m, c2, _r), c in classified.items() if c2 == cond]
        fault = [v for v in vals if task_meta.get(
            _case_of(classified, v), {}).get("task_type") == "fault"]
        # (simpler: recompute below)
    fault_cases = {t["task_id"] for t in manifest["tasks"]
                   if t["task_type"] == "fault"}
    for cond in ("A", "B"):
        vals = [c for k, c in classified.items() if k[2] == cond
                and k[0] in fault_cases]
        scored = [v for v in vals if v["dl_score"] is not None]
        summary.setdefault("fault_localization", {})[cond] = {
            "n_fault_obs": len(vals),
            "dl_score_distribution": {str(s): sum(1 for v in scored
                                                  if v["dl_score"] == s)
                                      for s in (0, 1, 2)},
            "dl_mean": round(statistics.mean(
                (v["dl_score"] for v in scored)), 3) if scored else None,
        }
    # repeat = 1: consistency not applicable; note it
    summary["repeat_consistency"] = "n/a (1 repeat per cell, per frozen plan)"
    (OUT / "holdout_metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items()
                      if k in ("observations_launched", "A", "B",
                               "fault_localization")}, ensure_ascii=False,
                     indent=2)[:3000])
    return 0


def _case_of(classified, v):
    for k, c in classified.items():
        if c is v:
            return k[0]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
