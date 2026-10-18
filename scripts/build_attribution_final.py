"""Final-round P2.1: re-attribute the 8 historical failure classes through
Failure Attribution 2.0 (attribution.py) with the FULL evidence chain and
real intervention experiments. Offline; reads frozen ledgers only.

Output: data/eval/final_round/p2_attribution/attribution_final.json
(with a slot for the P2.2 ablation results appended by run_p2_ablation.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from genomeqc_agent.evolution.attribution import apply_intervention, attribute

OUT = ROOT / "data" / "eval" / "final_round" / "p2_attribution"
V32_LEDGER = ROOT / "V3_2_REAL_AB_LEDGER.jsonl"
S2_LEDGER = ROOT / "V3_S2_RUN_LEDGER.jsonl"


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


v32 = load(V32_LEDGER)
s2 = load(S2_LEDGER)

s2_503 = [r for r in s2 if r.get("kind") == "observation"
          and r.get("status") == "failed"]
s2_repair = [r for r in s2 if r.get("kind") == "repair"]
invalid_a = [r for r in v32 if r.get("condition") == "A"
             and (r.get("unresolved_pointers") or 0) > 0]
invalid_b = [r for r in v32 if r.get("condition") == "B"
             and (r.get("unresolved_pointers") or 0) > 0]
b_partials = [r for r in v32 if r.get("condition") == "B"
              and (r.get("context") or {}).get("completeness") != "full"]

records: list[dict] = []

# ---- 1. SiliconFlow 503 -------------------------------------------------
row = s2_503[0]
rec = attribute(run_id=row.get("run_id", ""), case_id=row.get("case_id", ""),
                error_text=row.get("raw_error", ""), status="failed",
                model_usage=[], planner_kind="llm")
rec = apply_intervention(rec, {
    "factor": "infrastructure",
    "changed": False,
    "outcome": "improved",
    "experiment": "bounded repair rerun of the identical cell (frozen "
                  "protocol) succeeded with no code/prompt change",
    "evidence": [f"S2 ledger repair row repairs_original={r.get('repairs_original')}"
                 for r in s2_repair],
})
rec.update({
    "class": "SiliconFlow 503",
    "attribution_status": "experimentally_supported",
    "status_note": "provider-side transient capacity failure; identical "
                   "retry succeeded -> no model/prompt/agent blame; 119/120 "
                   "first-run success in S2, 0 infra failures in V3.2",
})
records.append(rec)

# ---- 2. Invalid evidence pointer ----------------------------------------
rec = attribute(
    run_id=invalid_a[0]["run_id"], case_id=invalid_a[0]["case_id"],
    error_text=f"planner cited an evidence pointer that failed mechanical "
               f"resolution ({invalid_a[0]['unresolved_pointers']} unresolved; "
               "e.g. artifacts/genome.fa without a locator)",
    status="succeeded", model_usage=[{"total_tokens": invalid_a[0]["tokens"]}],
    context_receipt={"completeness": "full"}, planner_kind="llm")
rec = apply_intervention(rec, {
    "factor": "context",
    "changed": True,
    "outcome": "improved" if not invalid_b else "unimproved",
    "experiment": "V3.2 P4 single-factor A/B (promptv1 both arms): A raw-json "
                  "context = 4/72 invalid pointers, B ContextBuilder v1 = "
                  "0/72; P1.2 audit ruled out evidence deletion (identical "
                  "citation profiles 516=516)",
    "evidence": [
        "V3_2_REAL_AB_LEDGER.jsonl rows with unresolved_pointers>0: "
        + ", ".join(f"{r['case_id']}:{r['model_key']}/{r['condition']}"
                    f"/r{r['repeat']}" for r in invalid_a),
        ("S1 golden: ds/A artifacts/genome.fa:1 hallucination vs ds/B clean "
        "(docs/S1_SMOKE_RECEIPT.md)"),
        "S2: A=1 vs B=0 (glm META-05 golden)",
        "P1 validity audit: data/eval/final_round/p1_audit/del_evidence_audit.json",
    ],
})
rec.update({
    "class": "Invalid evidence pointer",
    "attribution_status": "experimentally_supported",
    "status_note": "first-layer model_capability_error (context complete, "
                   "model invented a pointer); context-organisation "
                   "intervention removes it 4->0; P2.2 ablation (C/D arms) "
                   "appended below refines the causal factor",
    "repeat_consistency": "44/48 cells internally consistent - the 4 events "
                          "are stochastic (1/3 repeats each), not systematic",
})
records.append(rec)

# ---- 3. Tool schema / argument error ------------------------------------
rec = attribute(run_id="n/a", case_id="n/a",
                error_text="tool arguments violated the schema",
                status="failed", planner_kind="llm")
rec.update({
    "class": "Tool schema / argument error",
    "attribution_status": "no_field_instance_mechanism_tested",
    "status_note": "NO real instance in retained S1/S2/V3.2 ledgers (0 "
                   "planner contract violations, 0 schema errors across 280 "
                   "paid observations). Mechanism covered by PlannedAction "
                   "pydantic validation + policy gate; single-factor fix "
                   "path (disambiguate tool description) is regression-tested",
})
records.append(rec)

# ---- 4. Checkpoint recovery budget reset ---------------------------------
rec = attribute(run_id="n/a", case_id="n/a",
                error_text="budget guard recomputed from zero after "
                           "checkpoint resume across processes",
                status="failed", checkpoint_restored=True, planner_kind="llm")
rec.update({
    "class": "Checkpoint recovery budget reset",
    "attribution_status": "confirmed",
    "status_note": "REAL defect found during S2 resume (2026-10-18): the "
                   "guard restarted from zero on a restored process, risking "
                   "budget overrun; fixed by seeding guard.tokens from the "
                   "ledger (BudgetGuard.from_ledger); regression-locked",
    "fix_commit_evidence": "V3.1 final release audit P0/P1 (docs/"
                           "V3_1_FINAL_RELEASE_AUDIT.md), S2 resume receipts",
})
records.append(rec)

# ---- 5. ScriptedPlanner substitution ------------------------------------
rec = attribute(run_id="n/a", case_id="n/a",
                error_text="succeeded with zero billed tokens",
                status="succeeded", model_usage=[], planner_kind="scripted")
rec.update({
    "class": "ScriptedPlanner accidental substitution",
    "attribution_status": "confirmed",
    "status_note": "REAL incident: S1 first smoke run executed 16 cells "
                   "with the ScriptedPlanner because the runner omitted the "
                   "planner argument (tok=0 exposed it); all 16 VOIDed and "
                   "re-run with the real planner; guards added",
    "intervention_verified": True,
    "evidence_level": "confirmed",
    "evidence": ["docs/S1_SMOKE_RECEIPT.md (VOID record)",
                 ("tests/unit/test_release_audit.py::"
                 "test_scripted_planner_is_identifiable_and_not_a_real_run"),
                 "is_real_model_run() guard in eval/safety.py"],
})
records.append(rec)

# ---- 6. Scorer false-pass definition error -------------------------------
rec = attribute(run_id="n/a", case_id="n/a",
                error_text="grader/judgment conflation in audit scoring",
                status="failed", grader_error="audit-side FAULT_CASES "
                "prefix mismatch made critical-false-pass counts read 0")
rec.update({
    "class": "Scorer false-pass definition error",
    "attribution_status": "confirmed",
    "status_note": "REAL defect in audit tooling (S1/S2 score audits): "
                   "FAULT_CASES prefix mismatch silently zeroed cfp; and "
                   "the false-pass definition was corrected to "
                   "qc_decision=pass on fault cases ONLY (never "
                   "judgment=pass); both fixed with regression locks",
    "evidence": ["scripts/audit_s1_scores.py FAULT_CASES fix",
                 ("tests/unit/test_release_audit.py::"
                 "test_critical_false_pass_requires_qc_pass_on_fault_case"),
                 ("tests/unit/test_release_audit.py::"
                 "test_grader_judgment_pass_is_never_a_false_pass_input")],
})
records.append(rec)

# ---- 7. Context key evidence missing (harness crash) ---------------------
rec = attribute(run_id="n/a", case_id="n/a",
                error_text="node_build_packet raised FileNotFoundError on a "
                           "missing packet directory",
                status="failed", planner_kind="llm")
rec.update({
    "class": "Context key evidence missing",
    "attribution_status": "confirmed",
    "status_note": "REAL harness defect found by P3 robustness injection "
                   "(10 domains): missing packet_dir crashed the graph "
                   "instead of failing structurally; fixed to catch "
                   "OSError/KeyError -> structured failure; note: in the "
                   "paid A/B no context receipt ever reported "
                   "completeness=partial (B omitted=0), so the LLM-side "
                   "context_missing category has no field instance",
    "evidence": ["src/genomeqc_agent/runtime/graph.py node_build_packet fix",
                 "tests/integration/test_harness_robustness.py"],
})
records.append(rec)

# ---- 8. Tool/model return content anomaly --------------------------------
rec = attribute(run_id="n/a", case_id="n/a",
                error_text="provider returned finish_reason=length: GLM "
                           "reasoning tokens count against max_tokens",
                status="failed", planner_kind="llm")
rec.update({
    "class": "Tool/model return content anomaly",
    "attribution_status": "experimentally_supported",
    "status_note": "REAL provider behaviour probed 2026-10-17: GLM-5.3-Flash "
                   "always emits reasoning_content counted against "
                   "max_tokens; enable_thinking=false is IGNORED (single-"
                   "factor probe of the gateway config); gateway detects "
                   "truncation and raises an explicit error instead of "
                   "silently accepting truncated plans; runners set "
                   "max_tokens=8192 accordingly",
    "evidence": ["docs/G4_GLM_RECEIPT.md", "src/genomeqc_agent/model/gateway.py"],
})
records.append(rec)

OUT.mkdir(parents=True, exist_ok=True)
payload = {
    "generated_at": "2026-10-19",
    "framework": "Failure Attribution 2.0 (attribution.py)",
    "levels": ["observed", "suspected", "experimentally_supported",
               "confirmed", "no_field_instance_mechanism_tested"],
    "records": records,
    "ablation": None,  # filled by scripts/run_p2_ablation.py --merge
}
(OUT / "attribution_final.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"WROTE {OUT / 'attribution_final.json'} records={len(records)}")
for r in records:
    print(f"  {r['class']}: {r['attribution_status']} "
          f"({r['first_layer_category']}, conf={r['confidence']})")
