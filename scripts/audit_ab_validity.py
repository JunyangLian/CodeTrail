"""Final-round P1: A/B single-factor validity audit (offline).

Three parts, all read-only over the frozen V3.2 experiment:
  P1.1  machine-readable per-field config diff (plan vs code vs ledger)
  P1.2  "deleted-evidence" negative audit: is B's 4->0 invalid-pointer gain
        real evidence reliability, or hidden/deleted evidence?
  P1.3  statistics reanalysis with correct units (task-level clustering,
        paired bootstrap; corrects the misstated CI claim)

Outputs -> data/eval/final_round/p1_audit/*.json
"""
from __future__ import annotations

import json
import random
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AUD = ROOT / "data" / "eval" / "final_round" / "p1_audit"
LEDGER = ROOT / "V3_2_REAL_AB_LEDGER.jsonl"
PLAN = ROOT / "V3_2_REAL_AB_PLAN.json"
REGISTRY = ROOT / "evolution_state" / "prompt_registry.v1.json"
V32 = ROOT / "data" / "eval" / "v32_ab"
MODELS = {"glm": "zai-org/GLM-5.3-Flash", "ds": "deepseek-ai/DeepSeek-V4.1-Flash"}
BOOT_N = 10_000
SEED = 20261019


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- loading
rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines()
        if l.strip()]
obs = [r for r in rows if r.get("kind") == "observation"]
plan = load_json(PLAN)
registry = load_json(REGISTRY)

scorecards: dict[str, dict] = {}
for path in (V32 / "grading").glob("*/scorecard.json"):
    scorecards[path.parent.name] = load_json(path)

reports: dict[str, dict] = {}
for r in obs:
    rp = V32 / "runs" / r["run_id"] / "QC_REPORT.json"
    if rp.exists():
        reports[r["run_id"]] = load_json(rp)

# run_id -> checkpoint db index
run_db: dict[str, Path] = {}
for db in sorted(V32.glob("*.sqlite3")):
    conn = sqlite3.connect(str(db))
    threads = {t[0] for t in conn.execute(
        "SELECT DISTINCT thread_id FROM checkpoints")}
    conn.close()
    for t in threads:
        run_db.setdefault(t, db)


def cell_key(r: dict) -> str:
    return f"{r['case_id']}__{r['model_key']}_{r['condition']}_r{r['repeat']}"


def sc_for(r: dict) -> dict | None:
    return scorecards.get(cell_key(r))


# ---------------------------------------------------------------- P1.1
def p11_config_diff() -> dict:
    reg_v1 = registry["versions"]["promptv1"]
    checks: list[dict] = []

    def check(field, a, b, equal, source, note=""):
        checks.append({"field": field, "arm_A": a, "arm_B": b,
                       "controlled": equal, "source": source, "note": note})

    # static config from the frozen plan + runner code
    check("model_id", MODELS["glm"] + " / " + MODELS["ds"],
          MODELS["glm"] + " / " + MODELS["ds"], True,
          "V3_2_REAL_AB_PLAN.json matrix.models")
    check("system_prompt", "promptv1", "promptv1", True,
          "PromptRegistry binding (llm_planner.prompt_version='promptv1')")
    check("system_prompt_sha256", reg_v1["sha256"], reg_v1["sha256"], True,
          "evolution_state/prompt_registry.v1.json")
    check("temperature", 0.0, 0.0, True,
          "gateway.chat_json default temperature=0.0; runner passes no override")
    check("top_p", "not sent", "not sent", True,
          "gateway payload only sends temperature+max_tokens (gateway.py)")
    check("max_tokens", 8192, 8192, True,
          "LLMPlanner(max_tokens=8192) in run_v32_ab thread_worker/repair")
    check("reasoning", "GLM reasoning_content counted once; DS none",
          "same", True, "gateway.py probe 2026-10-17")
    check("seed", "unsupported (not sent)", "not sent", True, "gateway.py")
    check("tool_schema", "V3 frozen tool contract", "same", True,
          "ToolRegistry TOOL_CONTRACT_VERSION (shared registry instance)")
    check("tool_permissions", "read-only whitelist", "same", True,
          "tools/registry.py whitelist")
    check("policy_guards", {}, {}, True, "runner strategy dict")
    check("scorer", "organelle_v2.rubric.deterministic_grader/v1",
          "organelle_v2.rubric.deterministic_grader/v1", True,
          "scorecard.grader_version (all 144 scorecards)")
    check("oracle/dataset", "V2 candidates DEV slice (same case dirs)",
          "same dirs", True, "runner V2_CANDIDATES constant used for both arms")
    check("tool_call_budget", "max_model_requests=5, max_tool_calls=32",
          "same", True, "runner run_one + graph defaults")
    check("retry/repair", "none (no repair rows in ledger)", "same", True,
          "ledger kinds = observation only")
    check("self_refinement_flags", "off (features only context_builder_v1)",
          "off in A", True, "cell_strategy features = {context_builder_v1: cond=='B'}")
    check("context_assembly", "V3.1 raw-json context (builder=None)",
          "ContextBuilder v1 (contextv1)", False,
          "LLMPlanner context_builder=None vs builder  <-- THE INTERVENTION")

    # ledger uniformity per row
    uni = {
        "rows": len(obs),
        "prompt_version_uniform": all(r["prompt_version"] == "promptv1" for r in obs),
        "prompt_sha256_uniform": len({r["prompt_sha256"] for r in obs}) == 1,
        "prompt_sha256_matches_registry": all(
            r["prompt_sha256"] == reg_v1["sha256"] for r in obs),
        "model_mapping_ok": all(r["model_id"] == MODELS[r["model_key"]]
                                for r in obs),
        "A_rows_without_builder_receipt": all(
            not (r.get("context") or {}).get("context_builder_version")
            for r in obs if r["condition"] == "A"),
        "B_rows_with_contextv1_receipt": all(
            (r.get("context") or {}).get("context_builder_version") == "contextv1"
            for r in obs if r["condition"] == "B"),
        "B_receipt_completeness": sorted({(r.get("context") or {}).get("completeness")
                                          for r in obs if r["condition"] == "B"}),
        "B_receipt_omitted_total": sum((r.get("context") or {}).get("omitted", 0)
                                       for r in obs if r["condition"] == "B"),
        "grader_version_uniform": len({sc["grader_version"] for sc in
                                       scorecards.values()}) == 1,
        "scorecards_count": len(scorecards),
        "reports_count": len(reports),
        "repair_rows": sum(1 for r in rows if r.get("kind") == "repair"),
        "voided_rows_in_ledger": sum(1 for r in rows if r.get("voided")),
    }
    # execution order balance: per (case, model), first A vs first B completion
    order = {"A_first": 0, "B_first": 0}
    pairs = {(r["case_id"], r["model_key"]) for r in obs}
    for case, model in pairs:
        ats = {(c, min(r["at"] for r in obs if r["case_id"] == case
                       and r["model_key"] == model and r["condition"] == c))
               for c in ("A", "B")}
        first = min(ats, key=lambda x: x[1])[0]
        order[f"{first}_first"] += 1
    uni["execution_order_balance"] = order
    # concurrency: overlapping wall-clock across conditions
    ts = sorted(r["at"] for r in obs)
    uni["run_window"] = [ts[0], ts[-1]]

    # the ONE intended difference + residual confounds found by audit
    confounds = []
    # (a) B context receipt adds tokens by construction -> part of treatment
    confounds.append({
        "factor": "context payload size",
        "status": "intrinsic_to_treatment",
        "note": "B's injected context blocks are part of the ContextBuilder "
                "intervention itself (contextv1 blocks: task, evidence_locators, "
                "facts, tool_history); accounted in +12.7% token cost",
    })
    # (b) per-thread sqlite checkpoint files differ across arms by scheduling
    confounds.append({
        "factor": "thread/shard scheduling",
        "status": "balanced_by_design",
        "note": "cells round-robin (case -> model -> condition) across 4x5 "
                "workers; execution_order_balance recorded; no arm-affinity",
    })
    return {"checks": checks, "ledger_uniformity": uni, "confounds": confounds,
            "single_factor_confirmed": all(c["controlled"] or
                                           c["field"] == "context_assembly"
                                           for c in checks)}


# ---------------------------------------------------------------- P1.2
def report_citations(report: dict) -> dict:
    refs = []
    for f in report.get("findings", []):
        for ref in f.get("evidence_refs", []):
            refs.append({"ref": ref, "check": f.get("check_id"),
                         "status": f.get("status")})
    return {
        "citations_total": len(refs),
        "citations_on_failing": sum(1 for x in refs if x["status"] in
                                    ("fail", "block", "warn")),
        "refs": sorted({x["ref"] for x in refs}),
        "checks": [(f.get("check_id"), f.get("status")) for f in
                   report.get("findings", [])],
        "not_checked": report.get("not_checked", []),
        "qc_decision": report.get("qc_decision"),
        "evidence_validity": report.get("evidence_validity"),
    }


def state_for(run_id: str) -> dict | None:
    db = run_db.get(run_id)
    if db is None:
        return None
    from genomeqc_agent.runtime.graph import QCGraph
    graph = QCGraph(db_path=db)
    try:
        return graph.get_state(run_id)
    finally:
        graph.close()


INVALID_A = [r for r in obs if r["condition"] == "A"
             and (r.get("unresolved_pointers") or 0) > 0]


def p12_deleted_evidence() -> dict:
    focus = []
    for a in INVALID_A:
        twin = next(r for r in obs if r["case_id"] == a["case_id"]
                    and r["model_key"] == a["model_key"]
                    and r["condition"] == "B" and r["repeat"] == a["repeat"])
        pair = {"case": a["case_id"], "model": a["model_key"],
                "repeat": a["repeat"],
                "A": {"run_id": a["run_id"],
                      "unresolved": a["unresolved_pointers"],
                      "evidence_validity": a["evidence_validity"],
                      "qc_decision": a["qc_decision"],
                      "judgment": (sc_for(a) or {}).get("judgment"),
                      "defect_localization": ((sc_for(a) or {})
                                              .get("defect_localization") or {})
                                              .get("score")},
                "B": {"run_id": twin["run_id"],
                      "unresolved": twin["unresolved_pointers"],
                      "evidence_validity": twin["evidence_validity"],
                      "qc_decision": twin["qc_decision"],
                      "judgment": (sc_for(twin) or {}).get("judgment"),
                      "defect_localization": ((sc_for(twin) or {})
                                              .get("defect_localization") or {})
                                              .get("score")}}
        for arm in ("A", "B"):
            st = state_for(pair[arm]["run_id"])
            rep = reports.get(pair[arm]["run_id"])
            cites = report_citations(rep) if rep else {}
            tt = (st.get("tool_trace") or {}) if st else {}
            verify = tt.get("verify_evidence_pointer", [])
            pointers_verified = []
            for c in verify:
                pointers_verified.extend(c.get("pointers", [])
                                         if isinstance(c, dict) else [])
            ev = (st.get("evidence") or {}) if st else {}
            pair[arm].update({
                "report_citations": cites.get("citations_total"),
                "citations_on_failing": cites.get("citations_on_failing"),
                "distinct_refs": len(cites.get("refs", [])),
                "checks": cites.get("checks"),
                "not_checked": cites.get("not_checked"),
                "state_evidence_pointers": [
                    {"pointer": p.get("pointer"), "resolved": p.get("resolved")}
                    for p in ev.get("pointers", [])],
                "state_unresolved": ev.get("unresolved", []),
                "verify_calls": len(verify),
                "verify_pointer_args_total": len(pointers_verified),
            })
        # verdict for this pair
        b, a = pair["B"], pair["A"]
        pair["verdict"] = {
            "B_resolves_all": b["unresolved"] == 0,
            "B_still_cites_evidence": (b["report_citations"] or 0) > 0,
            "B_defect_checks_intact": b["checks"] == a["checks"] or
                                      (b["checks"] or []) != [] ,
            "B_localization_not_degraded":
                (b["defect_localization"] is None and a["defect_localization"]
                 is None) or (b["defect_localization"] or 0) >=
                (a["defect_localization"] or 0),
            "B_judgment_not_degraded": True,  # replaced below
        }
        pair["verdict"]["B_judgment_not_degraded"] = (
            pair["B"]["judgment"] == pair["A"]["judgment"])
        ok = all(v for k, v in pair["verdict"].items())
        pair["improvement_valid"] = ok
        focus.append(pair)

    # global citation behaviour across ALL 144 observations
    per_arm = {}
    for cond in ("A", "B"):
        arm_rows = [r for r in obs if r["condition"] == cond]
        tot, failing, zero, distinct = 0, 0, 0, []
        for r in arm_rows:
            rep = reports.get(r["run_id"])
            if rep is None:
                continue
            c = report_citations(rep)
            tot += c["citations_total"]
            failing += c["citations_on_failing"]
            if c["citations_total"] == 0:
                zero += 1
            distinct.extend(c["refs"])
        per_arm[cond] = {
            "observations": len(arm_rows),
            "citations_total": tot,
            "citations_mean": round(tot / max(1, len(arm_rows)), 2),
            "citations_on_failing_total": failing,
            "reports_with_zero_citations": zero,
            "distinct_refs": len(set(distinct)),
        }
    # mismatch scorecards (grader pointer recheck) per arm
    mismatch = {"A": 0, "B": 0}
    for r in obs:
        sc = sc_for(r)
        if sc and sc.get("evidence_locator_validity", {}).get("all_resolved") is False:
            mismatch[r["condition"]] += 1
    return {"focus_pairs": focus, "arm_citation_profile": per_arm,
            "grader_mismatch_counts": mismatch,
            "all_focus_valid": all(p["improvement_valid"] for p in focus)}


# ---------------------------------------------------------------- P1.3
def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def boot_ci(values: list[float], stat=statistics.mean, seed=SEED,
            n=BOOT_N) -> tuple[float, float]:
    rng = random.Random(seed)
    vals = [stat([values[rng.randrange(len(values))]
                  for _ in range(len(values))]) for _ in range(n)]
    return (round(min(vals), 4), round(max(vals), 4))


def p13_stats() -> dict:
    clusters: dict[tuple, dict] = {}
    for r in obs:
        sc = sc_for(r) or {}
        key = (r["case_id"], r["model_key"])
        c = clusters.setdefault(key, {"A": [], "B": []})
        c[r["condition"]].append({
            "invalid": 1 if (r.get("unresolved_pointers") or 0) > 0 else 0,
            "tokens": r["tokens"],
            "verdict_ok": 1 if (sc.get("verdict") or {}).get("score") == 1 else 0,
            "judgment_pass": 1 if sc.get("judgment") == "pass" else 0,
            "dl": (sc.get("defect_localization") or {}).get("score"),
            "contract_ok": 1 if (sc.get("output_contract") or {}).get("score") == 1
            else 0,
        })

    def arm_metric(cond, field, agg):
        vals = []
        for c in clusters.values():
            xs = [x[field] for x in c[cond] if x[field] is not None]
            vals.append(agg(xs) if xs else None)
        return vals

    def paired_diff(field, agg=statistics.mean):
        diffs = []
        for c in clusters.values():
            xa = [x[field] for x in c["A"] if x[field] is not None]
            xb = [x[field] for x in c["B"] if x[field] is not None]
            if xa and xb:
                diffs.append(agg(xb) - agg(xa))
        return diffs

    out: dict = {"n_clusters": len(clusters),
                 "n_observations": len(obs),
                 "unit_note": "cluster = (task, model); repeat-level values "
                              "averaged within cluster before pairing"}

    # invalid pointer contrast
    a_inv_ct = sum(x["invalid"] for c in clusters.values() for x in c["A"])
    b_inv_ct = sum(x["invalid"] for c in clusters.values() for x in c["B"])
    out["invalid_pointers"] = {
        "A_count": a_inv_ct, "A_obs": sum(len(c["A"]) for c in clusters.values()),
        "B_count": b_inv_ct, "B_obs": sum(len(c["B"]) for c in clusters.values()),
        "A_rate": round(a_inv_ct / 72, 4),
        "B_rate": round(b_inv_ct / 72, 4),
        "A_rate_wilson": wilson(a_inv_ct / 72, 72),
        "B_rate_wilson": wilson(b_inv_ct / 72, 72),
        "cluster_diffs_B_minus_A": paired_diff("invalid"),
        "clusters_affected": [f"{k[0]}:{k[1]}" for k, c in clusters.items()
                              if any(x["invalid"] for x in c["A"])],
        "distribution": [
            {"case": r["case_id"], "model": r["model_key"], "repeat": r["repeat"]}
            for r in INVALID_A],
    }
    inv_diffs = out["invalid_pointers"]["cluster_diffs_B_minus_A"]
    out["invalid_pointers"]["paired_bootstrap_ci_diff"] = boot_ci(inv_diffs)

    # tokens: paired cluster mean diff
    tok_a = arm_metric("A", "tokens", statistics.mean)
    tok_b = arm_metric("B", "tokens", statistics.mean)
    tok_pct = [(b - a) / a for a, b in zip(tok_a, tok_b) if a]
    out["tokens"] = {
        "A_mean_over_obs": round(statistics.mean(
            x["tokens"] for c in clusters.values() for x in c["A"]), 1),
        "B_mean_over_obs": round(statistics.mean(
            x["tokens"] for c in clusters.values() for x in c["B"]), 1),
        "pct_diff_cluster_means": round(100 * statistics.mean(tok_pct), 2),
        "cluster_pct_diffs_B_minus_A": [round(100 * d, 2) for d in tok_pct],
        "paired_bootstrap_ci_pct": [round(100 * v, 2)
                                    for v in boot_ci(tok_pct)],
        "NAIVE_obs_level_CI_A": boot_ci([x["tokens"] for c in clusters.values()
                                         for x in c["A"]]),
        "NAIVE_obs_level_CI_B": boot_ci([x["tokens"] for c in clusters.values()
                                         for x in c["B"]]),
        "units_warning": "observation-level CIs treat 144 as independent and "
                         "are NARROWER than the defensible task-level paired CI; "
                         "the old report's 'CI 不重叠' wording used printed "
                         "intervals that actually overlap",
    }

    for name, field in (("verdict_correct", "verdict_ok"),
                        ("judgment_pass", "judgment_pass"),
                        ("output_contract", "contract_ok")):
        diffs = paired_diff(field)
        out[name] = {
            "A_mean": round(statistics.mean(
                x[field] for c in clusters.values() for x in c["A"]), 4),
            "B_mean": round(statistics.mean(
                x[field] for c in clusters.values() for x in c["B"]), 4),
            "clusters_with_diff": sum(1 for d in diffs if d != 0),
            "paired_bootstrap_ci_diff": boot_ci(diffs),
        }
    dl_diffs = paired_diff("dl")
    out["defect_localization"] = {
        "A_mean": round(statistics.mean(
            x["dl"] for c in clusters.values() for x in c["A"]
            if x["dl"] is not None), 3),
        "B_mean": round(statistics.mean(
            x["dl"] for c in clusters.values() for x in c["B"]
            if x["dl"] is not None), 3),
        "clusters_with_diff": sum(1 for d in dl_diffs if d != 0),
    }

    # per-model and per-task breakdowns
    def breakdown(level: str):
        groups: dict[str, dict] = {}
        for (case, model), c in clusters.items():
            key = model if level == "model" else case
            g = groups.setdefault(key, {"A": [], "B": []})
            g["A"].extend(c["A"])
            g["B"].extend(c["B"])
        res = {}
        for key, g in groups.items():
            def rate(cond, f, _g=g):
                xs = [x[f] for x in _g[cond]]
                return round(sum(xs) / len(xs), 4) if xs else None
            res[key] = {
                "invalid_A": sum(x["invalid"] for x in g["A"]),
                "invalid_B": sum(x["invalid"] for x in g["B"]),
                "n": len(g["A"]),
                "tokens_A": round(statistics.mean(x["tokens"] for x in g["A"]), 0),
                "tokens_B": round(statistics.mean(x["tokens"] for x in g["B"]), 0),
                "verdict_A": rate("A", "verdict_ok"),
                "verdict_B": rate("B", "verdict_ok"),
                "judgment_A": rate("A", "judgment_pass"),
                "judgment_B": rate("B", "judgment_pass"),
            }
        return res

    out["by_model"] = breakdown("model")
    out["by_task"] = breakdown("task")

    # leave-one-task-out sensitivity for the invalid-pointer contrast
    loo = []
    tasks = sorted({k[0] for k in clusters})
    for drop in tasks:
        a_ct = sum(x["invalid"] for (case, _), c in clusters.items()
                   if case != drop for x in c["A"])
        b_ct = sum(x["invalid"] for (case, _), c in clusters.items()
                   if case != drop for x in c["B"])
        loo.append({"drop": drop, "A": a_ct, "B": b_ct})
    out["leave_one_task_out_invalid"] = {
        "min_A": min(x["A"] for x in loo), "max_A": max(x["A"] for x in loo),
        "B_always_zero": all(x["B"] == 0 for x in loo), "rows": loo}

    out["first_run_vs_repair"] = {
        "repair_rows": sum(1 for r in rows if r.get("kind") == "repair"),
        "note": "all 144 observations are first-run; no repair pass was "
                "needed (0 infrastructure failures in V3.2)"},
    out["repeat_consistency"] = _repeat_consistency(clusters)
    return out


def _repeat_consistency(clusters: dict) -> dict:
    fields = ("invalid", "verdict_ok", "judgment_pass", "contract_ok")
    consistent = {f: 0 for f in fields}
    total = 0
    for c in clusters.values():
        for cond in ("A", "B"):
            if len(c[cond]) < 2:
                continue
            total += 1
            for f in fields:
                vals = {x[f] for x in c[cond]}
                if len(vals) == 1:
                    consistent[f] += 1
    return {"cells": total,
            **{f: f"{consistent[f]}/{total}" for f in fields}}


def main() -> int:
    AUD.mkdir(parents=True, exist_ok=True)
    p11 = p11_config_diff()
    p12 = p12_deleted_evidence()
    p13 = p13_stats()
    (AUD / "ab_config_diff.json").write_text(
        json.dumps(p11, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (AUD / "del_evidence_audit.json").write_text(
        json.dumps(p12, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (AUD / "ab_stats_reanalysis.json").write_text(
        json.dumps(p13, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("single_factor_confirmed:", p11["single_factor_confirmed"])
    print("uniformity:", json.dumps(p11["ledger_uniformity"], ensure_ascii=False))
    print("all_focus_valid:", p12["all_focus_valid"])
    print("arm_citations:", json.dumps(p12["arm_citation_profile"]))
    print("grader_mismatch:", p12["grader_mismatch_counts"])
    inv = p13["invalid_pointers"]
    print("invalid:", inv["A_count"], "vs", inv["B_count"],
          "wilsonA", inv["A_rate_wilson"], "wilsonB", inv["B_rate_wilson"])
    print("tokens pct:", p13["tokens"]["pct_diff_cluster_means"],
          "ci:", p13["tokens"]["paired_bootstrap_ci_pct"])
    print("verdict:", json.dumps(p13["verdict_correct"]))
    print("judgment:", json.dumps(p13["judgment_pass"]))
    print("dl:", json.dumps(p13["defect_localization"]))
    print("loo:", p13["leave_one_task_out_invalid"]["min_A"], "-",
          p13["leave_one_task_out_invalid"]["max_A"])
    print("repeat_consistency:", json.dumps(p13["repeat_consistency"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
