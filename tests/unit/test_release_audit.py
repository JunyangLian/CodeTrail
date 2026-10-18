"""V3.1 release-audit regression tests (P1).

Five contracts added per the release-audit directive:
1. cross-process resume: the global budget guard is seeded from the ledger
2. critical false pass = qc_decision=pass on a fault case (judgment=pass on
   a correctly blocked fault case must NOT count)
3. ScriptedPlanner can never masquerade as a real model run
4. a repair never overwrites the original failure or its billing
5. a simulated approval can never trigger a real publish
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from genomeqc_agent.eval.safety import (
    critical_false_pass,
    is_fault_case,
    is_real_model_run,
)
from genomeqc_agent.evolution.budget import BudgetGuard, ledger_billing
from genomeqc_agent.evolution.controller import (
    EvolutionController,
    EvolutionError,
)
from genomeqc_agent.model.scripted_planner import ScriptedPlanner

BASE_METRICS = {"judgment_pass_rate": 0.85, "pointer_resolution_rate": 0.99,
                "critical_false_pass": 0, "denominator": 40}


# -- 1. budget restore from ledger ----------------------------------------------

def _write_ledger(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")
    return path


def test_budget_guard_restores_from_ledger(tmp_path):
    ledger = _write_ledger(tmp_path / "ledger.jsonl", [
        {"kind": "observation", "case_id": "c1", "arm": "glm_A",
         "tokens": 5000, "requests": 3, "status": "succeeded"},
        {"kind": "observation", "case_id": "c1", "arm": "glm_B",
         "tokens": 6000, "requests": 3, "status": "failed"},
        {"kind": "repair", "case_id": "c1", "arm": "glm_B",
         "tokens": 7000, "requests": 2, "status": "succeeded",
         "repairs_original": "orig"},
    ])
    guard = BudgetGuard.from_ledger(ledger, token_cap=1_000_000,
                                    request_cap=390)
    assert guard.tokens == 18_000  # 5000 + 6000 + 7000: repair bills too
    assert guard.requests == 8
    # the seeded guard enforces the cap on top of history
    assert guard.reserve(1_000_000, 1) is False  # would blow the token cap
    assert guard.reserve(100, 1) is True


def test_budget_guard_empty_ledger_is_zero(tmp_path):
    guard = BudgetGuard.from_ledger(tmp_path / "missing.jsonl",
                                    token_cap=100, request_cap=10)
    assert guard.tokens == 0 and guard.requests == 0


def test_ledger_billing_counts_observation_and_repair(tmp_path):
    ledger = _write_ledger(tmp_path / "ledger.jsonl", [
        {"kind": "observation", "tokens": 2500, "requests": 2},
        {"kind": "repair", "tokens": 8600, "requests": 3},
    ])
    assert ledger_billing(ledger) == (11_100, 5)  # both attempts billed


# -- 2. critical false pass definition -------------------------------------------

def test_critical_false_pass_requires_qc_pass_on_fault_case():
    fault = "chloro_C1_NC_013991_2_CHLORO-CDS-03"
    normal = "chloro_C1_NC_013991_2_CHLORO-NORMAL-00"
    # the ONLY true positive: the agent delivered pass on a fault case
    assert critical_false_pass("pass", fault) is True
    # correctly blocked fault case: NOT a false pass, whatever the grader said
    assert critical_false_pass("block", fault) is False
    # pass on a genuinely normal packet: correct behaviour, not a false pass
    assert critical_false_pass("pass", normal) is False
    # is_fault_case recognises all three frozen fault suffixes
    assert is_fault_case("chloro_C1_NC_082429_1_CHLORO-ID-01")
    assert is_fault_case("chloro_C1_NC_013991_2_CHLORO-META-05")


def test_grader_judgment_pass_is_never_a_false_pass_input(tmp_path):
    """Regression lock for the release-audit finding: a grader judgment=pass
    on a correctly blocked fault case was miscounted as a critical false
    pass in the first analysis draft. The safety helper takes qc_decision
    only - judgment cannot leak in."""
    fault = "chloro_C1_NC_013991_2_CHLORO-CDS-03"
    assert critical_false_pass("block", fault) is False  # judgment was "pass"


# -- 3. ScriptedPlanner cannot masquerade -----------------------------------------

def test_scripted_planner_is_identifiable_and_not_a_real_run():
    planner = ScriptedPlanner()
    assert planner.provider == "fake"  # identity is machine-checkable
    assert planner.model_id.startswith("fake")
    # a scripted run records no model usage: is_real_model_run is False
    assert is_real_model_run([]) is False
    assert is_real_model_run(None) is False


def test_real_llm_usage_is_recognised():
    assert is_real_model_run([{"total_tokens": 3063}]) is True
    # a failed attempt with 0 tokens does not make it a real observation
    assert is_real_model_run([{"total_tokens": 0}]) is False


# -- 4. repair never overwrites ---------------------------------------------------

def test_repair_rows_are_separate_and_original_preserved(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    original = {"kind": "observation", "case_id": "c1", "arm": "glm_B",
                "run_id": "orig-run", "status": "failed", "tokens": 2524,
                "requests": 2, "raw_error": "gateway 503"}
    _write_ledger(ledger, [original])
    repair = {"kind": "repair", "case_id": "c1", "arm": "glm_B",
              "run_id": "repair-run", "status": "succeeded", "tokens": 8610,
              "requests": 3, "repairs_original": "orig-run"}
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(repair) + "\n")
    lines = [json.loads(l) for l in
             ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
    assert lines[0]["status"] == "failed"      # original failure preserved
    assert lines[0]["tokens"] == 2524          # original billing preserved
    assert lines[1]["kind"] == "repair"        # separate kind
    assert lines[1]["repairs_original"] == "orig-run"  # linkage recorded
    assert ledger_billing(ledger) == (11_134, 5)  # both billed, none lost


# -- 5. simulated approval cannot publish ------------------------------------------

def test_simulated_approval_cannot_trigger_publish(tmp_path):
    controller = EvolutionController(tmp_path / "receipts",
                                     active_config={"max_tokens": 8192})
    controller.propose("C2", {"config_delta": {"max_tokens": 4096},
                              "rollback_ref": "{}",
                              "simulation": "offline_simulation"})
    controller.transition("C2", "validated", metrics={
        "judgment_pass_rate": 0.9, "pointer_resolution_rate": 1.0,
        "critical_false_pass": 0, "denominator": 40}, baseline=BASE_METRICS)
    controller.transition("C2", "shadow_tested")
    controller.transition("C2", "accepted")
    controller.record_approval("C2", approver="offline_simulation:driver",
                               decision="approve", real=False)
    with pytest.raises(EvolutionError, match="real human approval"):
        controller.transition("C2", "approved", human_approval=True)
    assert controller.active_config["max_tokens"] == 8192  # active untouched
    # only a REAL approval receipt unlocks the gate
    controller.record_approval("C2", approver="human:maintainer",
                               decision="approve", real=True)
    controller.transition("C2", "approved", human_approval=True)
    assert controller.state["C2"]["status"] == "approved"
