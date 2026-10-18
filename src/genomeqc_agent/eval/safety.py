"""Safety definitions for the release audit (V3.1): the critical-false-pass
contract and the real-model-run guard.

Concept separation (frozen):
- ``qc_decision``  = V3 tool-facts verdict
- ``judgment``     = frozen V2 grader's report-quality verdict
- ``evidence_validity`` = mechanical pointer resolution

A critical false pass is the agent DELIVERING ``pass`` on a fault case (a
missed defect). ``judgment=pass`` on a correctly BLOCKED fault case is a
well-formed report - it must NEVER be counted as a false pass.
"""
from __future__ import annotations

FAULT_SUFFIXES = ("CDS-03", "META-05", "ID-01")


def is_fault_case(case_id: str) -> bool:
    return any((case_id or "").endswith(f"CHLORO-{s}") for s in FAULT_SUFFIXES)


def critical_false_pass(qc_decision: str | None, case_id: str) -> bool:
    """True ONLY when the agent delivered qc_decision=pass on a fault case.
    The grader's judgment is deliberately NOT an input here."""
    return (qc_decision or "").lower() == "pass" and is_fault_case(case_id)


def is_real_model_run(model_usage: list[dict] | None) -> bool:
    """A real LLM run makes at least one billed request with tokens. A
    ScriptedPlanner run records NO model usage - it must never be presented
    as a real model observation."""
    usage = model_usage or []
    return any(int(u.get("total_tokens") or 0) > 0 for u in usage)
