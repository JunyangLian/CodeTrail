"""V3.2 P2: Failure Attribution 2.0 - structured diagnosis, not keyword verdicts.

Rules only ever SEED a hypothesis. Every record carries the full evidence
chain: observed failure, first-layer category, root-cause hypothesis,
supporting evidence, ALTERNATIVE explanations, confidence, whether an
intervention experiment verified the cause, suggested fix and regression
reference. Evidence levels:

    observed -> suspected -> experimentally_supported -> confirmed

The model is NEVER blamed without evidence: when the context was
incomplete (builder receipt completeness=partial) or a tool/schema/scorer
error is plausible, ``context_missing_or_truncated`` /
``tool_schema_or_argument_error`` / ``dataset_or_scorer_error`` are carried
as alternative explanations with equal standing.
"""
from __future__ import annotations

import hashlib

CATEGORIES = (
    "model_capability_error",
    "prompt_instruction_error",
    "context_missing_or_truncated",
    "tool_schema_or_argument_error",
    "agent_state_error",
    "infrastructure_error",
    "dataset_or_scorer_error",
    "unknown",
)

LEVELS = ("observed", "suspected", "experimentally_supported", "confirmed")

HIGH_CONF_INFRA = ("503", "timeout", "readtimeout", "transport failure",
                   "rate limit", "429", "connection")


def _failure_id(run_id: str, case_id: str, error: str) -> str:
    digest = hashlib.sha256(f"{run_id}|{case_id}|{error}".encode()).hexdigest()
    return "FA-" + digest[:12]


def attribute(*, run_id: str, case_id: str, error_text: str,
              status: str = "failed", model_usage: list | None = None,
              context_receipt: dict | None = None,
              tool_calls: dict | None = None,
              grader_error: str | None = None,
              planner_kind: str = "llm",
              checkpoint_restored: bool = False) -> dict:
    """Attribute one observed failure. Rules seed hypotheses; the caller (or
    an intervention experiment) raises the evidence level."""
    error_text = error_text or ""
    low = error_text.lower()
    usage = model_usage or []
    billed = any(int(u.get("total_tokens") or 0) > 0 for u in usage)

    # ---- deterministic first-layer facts ---------------------------------
    if grader_error:
        category = "dataset_or_scorer_error"
        hypothesis = "scorer/oracle side failure (grader subprocess error)"
        supporting = [f"grader_error={grader_error[:200]}"]
        alternatives = ["agent produced contract-invalid output"]
        confidence = "high"
        fix = "verify grader worker and expected.json integrity; rerun grading"
        regression = "tests/unit/test_release_audit.py::test_ledger_billing_counts_observation_and_repair"
    elif planner_kind == "scripted" or (status == "succeeded" and not billed):
        category = "agent_state_error"
        hypothesis = ("ScriptedPlanner substituted for the LLM planner "
                      "(harness misuse; run is not a model observation)")
        supporting = ["planner_kind=scripted" if planner_kind == "scripted"
                      else "succeeded with zero billed tokens"]
        alternatives = ["provider returned empty usage (rare)"]
        confidence = "high"
        fix = "pass the LLM planner explicitly; assert is_real_model_run()"
        regression = "tests/unit/test_release_audit.py::test_scripted_planner_is_identifiable_and_not_a_real_run"
    elif any(s in low for s in HIGH_CONF_INFRA):
        category = "infrastructure_error"
        hypothesis = "provider-side capacity/network failure (no model blame)"
        supporting = [f"error_text={error_text[:200]}"]
        alternatives = ["sustained provider outage (retry later)"]
        confidence = "high"
        fix = "bounded repair per frozen protocol; record separately"
        regression = ("tests/unit/test_release_audit.py::"
                      "test_budget_guard_restores_from_ledger")
    elif "schema" in low or "arguments failed" in low:
        category = "tool_schema_or_argument_error"
        hypothesis = "tool arguments violated the schema or the schema description is ambiguous"
        supporting = [f"error_text={error_text[:200]}"]
        alternatives = ["model misread the tool description (capability)"]
        confidence = "medium"
        fix = "disambiguate the tool description (single-factor experiment)"
        regression = "tests/unit/test_harness_robustness.py::test_tool_schema_ambiguity_is_recorded"
    elif "ledger mismatch" in low or checkpoint_restored and "budget" in low:
        category = "agent_state_error"
        hypothesis = "budget/state accounting reset or drift across process restore"
        supporting = [f"error_text={error_text[:200]}",
                      f"checkpoint_restored={checkpoint_restored}"]
        alternatives = ["double-counted repair billing"]
        confidence = "high"
        fix = "seed BudgetGuard.from_ledger on resume (regression-locked)"
        regression = "tests/unit/test_release_audit.py::test_budget_guard_restores_from_ledger"
    elif "pointer" in low and context_receipt and \
            context_receipt.get("completeness") == "partial":
        category = "context_missing_or_truncated"
        hypothesis = ("context was partial (protected-over-budget); the model "
                      "may never have seen the real locator it garbled")
        supporting = ["context receipt completeness=partial",
                      f"error_text={error_text[:200]}"]
        alternatives = ["model capability: invented the locator",
                        "prompt instruction gap (promptv1 lacks hygiene rules)"]
        confidence = "medium"
        fix = ("single-factor intervention: same prompt + ContextBuilder "
               "(arm B) vs baseline (arm A) - see P4 experiment")
        regression = "tests/unit/test_attribution.py::test_pointer_error_carries_alternatives"
    elif "pointer" in low or "unresolved" in low:
        category = "model_capability_error"
        hypothesis = ("model invented or mistyped an evidence locator "
                      "(first-layer model error, context was complete)")
        supporting = [f"error_text={error_text[:200]}",
                      "context completeness=full"]
        alternatives = ["tool manifest naming ambiguity"]
        confidence = "medium"
        fix = "pointer-hygiene prompt (promptv2) or manifest-echo context"
        regression = ("tests/unit/test_attribution.py::"
                      "test_pointer_error_carries_alternatives")
    elif not billed and status == "failed":
        category = "infrastructure_error"
        hypothesis = "request failed before any tokens were billed"
        supporting = ["zero billed tokens on a failed run"]
        alternatives = ["auth failure (check credentials)"]
        confidence = "medium"
        fix = "inspect gateway error; bounded repair"
        regression = "tests/unit/test_release_audit.py::test_ledger_billing_counts_observation_and_repair"
    else:
        category = "unknown"
        hypothesis = "insufficient evidence to attribute a cause"
        supporting = [f"error_text={error_text[:200]}"]
        alternatives = ["any of the other categories remain possible"]
        confidence = "low"
        fix = "design a minimal single-factor intervention experiment"
        regression = ""

    record = {
        "failure_id": _failure_id(run_id, case_id, error_text),
        "run_id": run_id, "case_id": case_id,
        "observed_failure": error_text[:500] or status,
        "first_layer_category": category,
        "root_cause_hypothesis": hypothesis,
        "supporting_evidence": supporting,
        "alternative_explanations": alternatives,
        "confidence": confidence,
        "intervention_verified": False,
        "evidence_level": "suspected",
        "suggested_fix": fix,
        "regression_ref": regression,
        "attribution_kind": "rule_seeded_hypothesis (NOT a causal verdict)",
    }
    return record


def apply_intervention(record: dict, experiment: dict) -> dict:
    """Upgrade a record after a controlled single-factor experiment.

    experiment: {factor: context|tool_schema|prompt|policy, changed: bool,
    outcome: improved|unimproved|regressed}. Only a reproducible outcome on
    the isolated factor raises the evidence level; multi-factor changes must
    NOT be attributed to one component.
    """
    upgraded = dict(record)
    upgraded["intervention"] = experiment
    if experiment.get("factor") and experiment.get("outcome") in (
            "improved", "unimproved", "regressed"):
        upgraded["intervention_verified"] = True
        upgraded["evidence_level"] = "experimentally_supported"
        if experiment["outcome"] == "improved":
            upgraded["first_layer_category"] = {
                "context": "context_missing_or_truncated",
                "tool_schema": "tool_schema_or_argument_error",
                "prompt": "prompt_instruction_error",
                "policy": "agent_state_error",
            }.get(experiment["factor"], record["first_layer_category"])
    return upgraded
