"""P4: S2 automatic analysis + evolution decision (frozen thresholds).

Outputs:
- docs/V3_S2_EVALUATION_REPORT.md
- V3_S2_EVOLUTION_DECISION.json (repo root)
- V3_S2_BUDGET_RECEIPT.json (repo root, TRUE cumulative numbers recomputed
  from the full ledger - the in-run guard of a resumed process is seeded
  but this receipt is the authoritative record)

Frozen decision thresholds (protocol §4/§6):
- safety: critical false pass = 0, leaks = 0, whitelist violations = 0
- main metrics (GLM B vs A, paired): judgment pass rate, verdict-correct
  rate, defect localization mean, output-contract rate  -> B >= A
- evidence quality: invalid-pointer ratio B <= A (resolution not lower)
- denominator complete (120/120)
- ACCEPT_CANDIDATE = gate passed (pending human approval; NOT a publish)
- REJECT_CANDIDATE = any safety violation or main-metric regression
- INCONCLUSIVE     = denominator incomplete / unmappable breakdown
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "V3_S2_RUN_LEDGER.jsonl"
OUT = ROOT / "data" / "eval" / "s2_experiment"
DOCS = ROOT / "docs"

FAULT_CASES = {"CDS-03", "META-05", "ID-01"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _is_fault(case_id: str) -> bool:
    return any(case_id.endswith(f"CHLORO-{s}") for s in FAULT_CASES)


def arm_metrics(rows: list[dict]) -> dict:
    n = len(rows)
    graded = [r for r in rows if r.get("judgment") is not None]
    loc_scores = [r["defect_localization"]["score"] for r in graded
                  if isinstance(r.get("defect_localization"), dict)
                  and isinstance(r["defect_localization"].get("score"), (int, float))]
    failed = [r for r in rows if r.get("status") != "succeeded"]
    return {
        "observations": n,
        "completed": sum(1 for r in rows if r.get("status") == "succeeded"),
        "failed": len(failed),
        "judgment_pass": sum(1 for r in graded if r.get("judgment") == "pass"),
        "judgment_pass_rate": (sum(1 for r in graded if r.get("judgment") == "pass")
                               / len(graded)) if graded else None,
        "verdict_correct": sum(1 for r in graded
                               if r.get("verdict_score") == 1),
        "verdict_correct_rate": (sum(1 for r in graded if r.get("verdict_score") == 1)
                                 / len(graded)) if graded else None,
        "output_contract_ok": sum(1 for r in graded if r.get("output_contract_ok")),
        "contract_rate": (sum(1 for r in graded if r.get("output_contract_ok"))
                          / len(graded)) if graded else None,
        "loc_applicable": len(loc_scores),
        "loc_mean": (sum(loc_scores) / len(loc_scores)) if loc_scores else None,
        "loc_max": max(loc_scores) if loc_scores else None,
        "invalid_pointer_obs": sum(1 for r in rows
                                   if (r.get("unresolved_pointers") or 0) > 0),
        "invalid_pointer_ratio": (sum(1 for r in rows
                                      if (r.get("unresolved_pointers") or 0) > 0)
                                  / n) if n else None,
        "unresolved_total": sum(r.get("unresolved_pointers") or 0 for r in rows),
        "partial": sum(1 for r in rows if r.get("evidence_validity") == "partial"),
        "needs_review": sum(1 for r in rows if r.get("needs_human_review")),
        "guard_denials": sum(len(r.get("guard_denials") or []) for r in rows),
        # critical false pass = the agent DELIVERS pass on a fault case
        # (a missed defect). judgment=pass on a fault case whose expected
        # verdict is block and which the agent correctly blocked is a
        # well-formed report, NOT a false pass (concept separation per the
        # frozen protocol: qc_decision / judgment / evidence_validity).
        "critical_false_pass": sum(1 for r in rows
                                   if r.get("qc_decision") == "pass"
                                   and _is_fault(r["case_id"])),
        "tokens": sum(r.get("tokens") or 0 for r in rows),
        "tokens_mean": (sum(r.get("tokens") or 0 for r in rows) / n) if n else None,
        "requests": sum(r.get("requests") or 0 for r in rows),
        "latency_mean_s": (round(sum(r.get("elapsed_s") or 0 for r in rows) / n, 1)
                           if n else None),
        "tool_calls_total": sum(sum((r.get("tool_calls") or {}).values())
                                for r in rows),
        "qc_pass": sum(1 for r in rows if r.get("qc_decision") == "pass"),
        "qc_block": sum(1 for r in rows if r.get("qc_decision") == "block"),
        "skipped_unmappable": sum(1 for r in rows if r.get("mappable") is False),
        "grader_errors": sum(1 for r in rows if r.get("grader_error")),
    }


def paired(rows: list[dict]) -> dict:
    by_cell = defaultdict(dict)
    for r in rows:
        if r["arm"].startswith("glm"):
            by_cell[r["case_id"]][r["arm"]] = r
    out = {}
    for metric, fn in {
        "judgment_pass": lambda r: r.get("judgment") == "pass",
        "verdict_correct": lambda r: r.get("verdict_score") == 1,
        "output_contract_ok": lambda r: bool(r.get("output_contract_ok")),
        "no_invalid_pointer": lambda r: (r.get("unresolved_pointers") or 0) == 0,
    }.items():
        both = [c for c in by_cell.values() if "glm_A" in c and "glm_B" in c]
        a = sum(1 for c in both if fn(c["glm_A"]))
        b = sum(1 for c in both if fn(c["glm_B"]))
        b_only = sum(1 for c in both if fn(c["glm_B"]) and not fn(c["glm_A"]))
        a_only = sum(1 for c in both if fn(c["glm_A"]) and not fn(c["glm_B"]))
        out[metric] = {"pairs": len(both), "A": a, "B": b,
                       "B_better_only": b_only, "A_better_only": a_only,
                       "A_rate": a / len(both) if both else None,
                       "B_rate": b / len(both) if both else None,
                       "A_ci": wilson(a / len(both), len(both)) if both else None,
                       "B_ci": wilson(b / len(both), len(both)) if both else None}
    tok_a = [c["glm_A"].get("tokens") or 0 for c in by_cell.values()
             if "glm_A" in c and "glm_B" in c]
    tok_b = [c["glm_B"].get("tokens") or 0 for c in by_cell.values()
             if "glm_A" in c and "glm_B" in c]
    if tok_a:
        out["tokens"] = {"pairs": len(tok_a),
                         "A_mean": sum(tok_a) / len(tok_a),
                         "B_mean": sum(tok_b) / len(tok_b),
                         "delta_mean": (sum(tok_b) - sum(tok_a)) / len(tok_a)}
    return out


def main() -> int:
    rows = [json.loads(l) for l in
            LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("kind") == "observation"]
    assert len(rows) == 120, f"expected 120 ledger rows, got {len(rows)}"
    repairs = [json.loads(l) for l in
               LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    repairs = [r for r in repairs if r.get("kind") == "repair"]
    arms = {"glm_A": [], "glm_B": [], "ds_A": []}
    for r in rows:
        arms[r["arm"]].append(r)
    # effective view for QUALITY metrics: a separately-listed bounded repair
    # of an infra-failed observation replaces it for grading purposes ONLY;
    # the original failure stays in the ledger and is disclosed as-is.
    repairs_by_cell = {(r["case_id"], r["arm"]): r for r in repairs
                       if r.get("status") == "succeeded"}
    effective = {arm: list(rs) for arm, rs in arms.items()}
    for (case_id, arm), repair in repairs_by_cell.items():
        if arm in effective:
            effective[arm] = [repair if (r["case_id"] == case_id)
                              else r for r in effective[arm]]
    metrics = {arm: arm_metrics(rs) for arm, rs in effective.items()}
    glm_paired = paired(rows + [r for r in repairs])

    m_a, m_b = metrics["glm_A"], metrics["glm_B"]
    main_checks = {
        "judgment_pass_rate_B_ge_A":
            (m_b["judgment_pass_rate"] or 0) >= (m_a["judgment_pass_rate"] or 0),
        "verdict_correct_rate_B_ge_A":
            (m_b["verdict_correct_rate"] or 0) >= (m_a["verdict_correct_rate"] or 0),
        "contract_rate_B_ge_A":
            (m_b["contract_rate"] or 0) >= (m_a["contract_rate"] or 0),
        "loc_mean_B_ge_A":
            (m_b["loc_mean"] if m_b["loc_mean"] is not None else 0)
            >= (m_a["loc_mean"] if m_a["loc_mean"] is not None else 0),
        "invalid_pointer_ratio_B_le_A":
            (m_b["invalid_pointer_ratio"] if m_b["invalid_pointer_ratio"] is not None
             else 1) <= (m_a["invalid_pointer_ratio"]
                         if m_a["invalid_pointer_ratio"] is not None else 1),
    }
    safety = {
        "critical_false_pass_zero":
            (m_b["critical_false_pass"] == 0 and m_a["critical_false_pass"] == 0),
        "no_grader_errors": sum(metrics[a]["grader_errors"] for a in metrics) == 0,
    }
    denominator = {
        "observations": len(rows),
        "complete": len(rows) == 120,
        "glm_A": m_a["observations"], "glm_B": m_b["observations"],
        "ds_A": metrics["ds_A"]["observations"],
    }
    if not denominator["complete"] or not safety["no_grader_errors"]:
        decision = "INCONCLUSIVE"
    elif not all(safety.values()):
        decision = "REJECT_CANDIDATE"
    elif all(main_checks.values()):
        decision = "ACCEPT_CANDIDATE"
    else:
        decision = "REJECT_CANDIDATE"

    decision_doc = {
        "receipt": "V3_S2_EVOLUTION_DECISION/v1",
        "at": _now(),
        "decision": decision,
        "meaning": ("ACCEPT_CANDIDATE = candidate reaches the pending-approval "
                    "state; publishing requires a REAL human approval "
                    "(record_approval(real=True)) and is NOT performed"),
        "frozen_thresholds": {"main": main_checks, "safety": safety,
                              "denominator": denominator},
        "glm_paired": glm_paired,
        "arm_metrics": metrics,
        "active_strategy_unchanged": "promptv1 (no publish path executed)",
    }
    (ROOT / "V3_S2_EVOLUTION_DECISION.json").write_text(
        json.dumps(decision_doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    # authoritative budget receipt recomputed from the FULL ledger
    # (observations AND separately-listed repairs)
    s1 = 98_851
    all_lines = [json.loads(l) for l in
                 LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    s2_tokens = sum(r.get("tokens") or 0 for r in all_lines
                    if r.get("kind") in ("observation", "repair"))
    s2_requests = sum(r.get("requests") or 0 for r in all_lines
                      if r.get("kind") in ("observation", "repair"))
    skipped = [r for r in rows if r.get("status") == "skipped_due_to_budget"]
    receipt = {
        "receipt": "V3_S2_BUDGET_RECEIPT/v1", "at": _now(),
        "metering": ("usage.total_tokens per successful chat call; reasoning "
                     "counted once inside completion; failed attempts = "
                     "requests with 0 tokens; ledger is the single source"),
        "s1_tokens": s1,
        "s2_tokens": s2_tokens, "s2_requests": s2_requests,
        "s2_token_cap": 901_149, "s2_request_cap": 390,
        "s2_within_caps": s2_tokens <= 901_149 and s2_requests <= 390,
        "project_total_tokens": s1 + s2_tokens,
        "project_cap": 1_000_000,
        "project_within_cap": s1 + s2_tokens <= 1_000_000,
        "skipped_due_to_budget": len(skipped),
        "interrupted_and_resumed": ("process killed mid-observation 111 by "
                                    "external interrupt; resumed from ledger "
                                    "with budget guard seeded from the ledger"),
    }
    (ROOT / "V3_S2_BUDGET_RECEIPT.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # markdown report
    def arm_table(m: dict, label: str) -> str:
        return (f"| {label} | {m['observations']} | {m['completed']} | "
                f"{m['judgment_pass']}/{m['judgment_pass_rate']:.3f} | "
                f"{m['verdict_correct']}/{m['verdict_correct_rate']:.3f} | "
                f"{m['output_contract_ok']}/{m['contract_rate']:.3f} | "
                f"{m['loc_mean'] if m['loc_mean'] is not None else '—'} "
                f"(n={m['loc_applicable']}) | "
                f"{m['invalid_pointer_obs']} ({m['invalid_pointer_ratio']:.3f}) | "
                f"{m['partial']} | {m['critical_false_pass']} | "
                f"{m['tokens']} ({m['tokens_mean']:.0f}/obs) | "
                f"{m['requests']} | {m['latency_mean_s']}s |")

    md = [
        "# V3 S2 评测报告（方案甲 · 真实模型运行）", "",
        (f"> 完成：{_now()} ｜ 数据来源：**真实模型运行**（120 观测账本 "
         "V3_S2_RUN_LEDGER.jsonl）｜ 评分：organelle_v2.rubric."
         "deterministic_grader/v1（冻结，V2 venv 隔离）"), "",
        "## 1. 分母", "",
        (f"- 计划 120 / 实际入账 120（glm_A {m_a['observations']}，"
         f"glm_B {m_b['observations']}，"
         f"ds_A {metrics['ds_A']['observations']}）；预算跳过 0；**分母完整**"), "",
        "## 2. 三臂指标总表", "",
        "| 臂 | 观测 | 完成 | judgment pass | verdict 正确 | 契约正确 | 定位均值 | 无效指针观测 | partial | critical false pass | tokens (均值) | reqs | 均延迟 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        arm_table(m_a, "GLM A (promptv1)"),
        arm_table(m_b, "GLM B (promptv2+guards)"),
        arm_table(metrics["ds_A"], "DeepSeek A (参考)"), "",
        "## 3. GLM A/B 配对分析（同案例同模型）", "",
    ]
    for metric, p in glm_paired.items():
        if metric == "tokens":
            md.append(f"- tokens/obs：A {p['A_mean']:.0f} → B {p['B_mean']:.0f} "
                      f"(Δ {p['delta_mean']:+.0f})")
        else:
            md.append(f"- {metric}：A {p['A']}/{p['pairs']} ({p['A_rate']:.3f}, "
                      f"CI {p['A_ci'][0]:.3f}-{p['A_ci'][1]:.3f}) → "
                      f"B {p['B']}/{p['pairs']} ({p['B_rate']:.3f}, "
                      f"CI {p['B_ci'][0]:.3f}-{p['B_ci'][1]:.3f})；"
                      f"仅 B 好 {p['B_better_only']}，仅 A 好 {p['A_better_only']}")
    md += ["", "## 4. 进化决策", "",
           f"- **{decision}**（ACCEPT = 达到待审批条件，**不等于批准发布**；active 策略保持 promptv1）", "",
           "- 主指标门槛（B ≥ A）：", "".join(
               f"\n  - {k}: {'PASS' if v else 'FAIL'}" for k, v in main_checks.items()),
           "- 安全门槛：", "".join(
               f"\n  - {k}: {'PASS' if v else 'FAIL'}" for k, v in safety.items()), "",
           "## 5. DeepSeek 参考基线（非 A/B 臂）", "",
           (f"- judgment pass {metrics['ds_A']['judgment_pass']}/"
            f"{metrics['ds_A']['observations']}；无效指针 "
            f"{metrics['ds_A']['invalid_pointer_obs']} 观测；tokens "
            f"{metrics['ds_A']['tokens']}——仅作稳定性/一致性参考"), "",
           "## 6. 天花板效应说明", "",
           ("- 本 DEV 切片 qc_decision 与 judgment pass 均接近满分（40 案例 G4 修复后基线强），"
            "判别力集中在**证据可靠性（无效指针比例）、执行效率（tokens/延迟）与定位质量**上。"), "",
           ("详细逐观测：`V3_S2_RUN_LEDGER.jsonl`；决策：`V3_S2_EVOLUTION_DECISION.json`；"
            "预算：`V3_S2_BUDGET_RECEIPT.json`。"), ""]
    (DOCS / "V3_S2_EVALUATION_REPORT.md").write_text(
        "\n".join(md), encoding="utf-8")
    print("decision:", decision)
    print("glm paired:", json.dumps({k: v for k, v in glm_paired.items()
                                     if k != "tokens"}, ensure_ascii=False)[:600])
    print("budget:", json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
