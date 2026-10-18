"""V3.2 P2.2: diagnostic regression suite built from EIGHT real historical
failures. Each case asserts the first-layer category, that hypotheses carry
alternatives, and that the model is never blamed without evidence."""
from __future__ import annotations

import pytest

from genomeqc_agent.evolution.attribution import (
    CATEGORIES,
    apply_intervention,
    attribute,
)

# 1. SiliconFlow 503 (real: S2 glm_B NC_088771_1 CHLORO-ID-01)
R_503 = {"run_id": "r1", "case_id": "c1",
             "error_text": 'gateway: provider returned 503: {"code":50508,'
                        '"message":"System is too busy now."}',
             "status": "failed", "model_usage": [{"total_tokens": 2524}]}


# 2. hallucinated evidence pointer (real: ds/A META-05 "genome.fa:1")
R_POINTER = {"run_id": "r2", "case_id": "chloro_C1_NC_013991_2_CHLORO-META-05",
                 "error_text": "unresolved pointer: genome.fa:1 (missing_file)",
                 "status": "succeeded",
                 "model_usage": [{"total_tokens": 5775}]}


# 3. budget guard reset after checkpoint resume (real: S2 resume incident)
R_BUDGET = {"run_id": "r3", "case_id": "c3",
                "error_text": "token ledger mismatch: checkpoint 624432 vs "
                           "guard 4600",
                "checkpoint_restored": True}


# 4. S1 accidental ScriptedPlanner (real: 16 mock observations, VOID)
R_SCRIPTED = {"run_id": "r4", "case_id": "c4", "error_text": "", "status": "succeeded",
                  "model_usage": [], "planner_kind": "scripted"}


# 5. critical false pass scoring semantics (real: FAULT_CASES prefix bug)
R_CFP = {"run_id": "r5", "case_id": "c5", "error_text": "",
             "grader_error": "FAULT_CASES prefix mismatch made cfp always 0"}


# 6. correct filename, unresolvable locator (real golden case class)
R_LOCATOR = {"run_id": "r6",
                 "case_id": "chloro_C1_NC_088770_1_CHLORO-CDS-03",
                 "error_text": "unresolved pointer: artifacts/proteins.faa:"
                            "all_sequences (locator_miss)", "status": "succeeded"}


# 7. tool arguments violate schema (real registry error class)
R_SCHEMA = {"run_id": "r7", "case_id": "c7",
                "error_text": "arguments failed schema: pointers min_length 1",
                "status": "failed"}


# 8. context compressed, key evidence lost (V3.2 builder partial)
R_CONTEXT = {"run_id": "r8", "case_id": "c8",
                 "error_text": "unresolved pointer: genome.fa:1",
                 "status": "succeeded",
                 "context_receipt": {"completeness": "partial"},
                 "model_usage": [{"total_tokens": 4000}]}


ALL = [R_503, R_POINTER, R_BUDGET, R_SCRIPTED, R_CFP, R_LOCATOR, R_SCHEMA,
       R_CONTEXT]


@pytest.mark.parametrize("record", ALL, ids=[
    "503", "pointer", "budget_reset", "scripted", "cfp_semantics",
    "locator_miss", "schema", "context_partial"])
def test_all_real_failures_produce_structured_records(record):
    out = attribute(**record)
    assert out["first_layer_category"] in CATEGORIES
    for field in ("failure_id", "run_id", "case_id", "observed_failure",
                  "root_cause_hypothesis", "supporting_evidence",
                  "alternative_explanations", "confidence",
                  "intervention_verified", "evidence_level",
                  "suggested_fix", "regression_ref"):
        assert field in out, field
    assert out["evidence_level"] == "suspected"  # rules never confirm
    assert out["attribution_kind"].startswith("rule_seeded")


def test_503_is_infrastructure_high_confidence_no_model_blame():
    out = attribute(**R_503)
    assert out["first_layer_category"] == "infrastructure_error"
    assert out["confidence"] == "high"
    # the hypothesis locates the cause at the provider, not the model
    assert "provider" in out["root_cause_hypothesis"]


def test_pointer_error_carries_alternatives():
    out = attribute(**R_POINTER)
    assert out["first_layer_category"] == "model_capability_error"
    assert out["alternative_explanations"]  # manifest ambiguity listed
    assert out["confidence"] != "high"  # not enough to confirm model blame


def test_context_partial_upgrades_context_category():
    out = attribute(**R_CONTEXT)
    assert out["first_layer_category"] == "context_missing_or_truncated"
    assert out["confidence"] == "medium"


def test_budget_reset_is_agent_state_error():
    out = attribute(**R_BUDGET)
    assert out["first_layer_category"] == "agent_state_error"


def test_scripted_planner_is_agent_state_error_high_confidence():
    out = attribute(**R_SCRIPTED)
    assert out["first_layer_category"] == "agent_state_error"
    assert out["confidence"] == "high"


def test_scorer_error_is_dataset_or_scorer():
    out = attribute(**R_CFP)
    assert out["first_layer_category"] == "dataset_or_scorer_error"


def test_schema_error_has_single_factor_fix():
    out = attribute(**R_SCHEMA)
    assert out["first_layer_category"] == "tool_schema_or_argument_error"
    assert "single-factor" in out["suggested_fix"] or \
        "disambiguate" in out["suggested_fix"]


def test_intervention_experiment_upgrades_level_and_never_multifactor():
    out = attribute(**R_POINTER)
    upgraded = apply_intervention(out, {
        "factor": "context", "changed": True, "outcome": "improved",
        "experiment": "same prompt + ContextBuilder (single factor)"})
    assert upgraded["intervention_verified"] is True
    assert upgraded["evidence_level"] == "experimentally_supported"
    assert upgraded["first_layer_category"] == "context_missing_or_truncated"
    # no experiment -> stays suspected (never confirmed)
    assert attribute(**R_POINTER)["evidence_level"] == "suspected"
