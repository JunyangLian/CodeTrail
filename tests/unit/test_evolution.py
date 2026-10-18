"""V3.1 evolution unit tests (M1-M4, offline simulation only)."""
from __future__ import annotations

import json

import pytest

from genomeqc_agent.evolution.controller import (
    EvolutionController,
    EvolutionError,
    StrategyProposer,
)
from genomeqc_agent.evolution.failure_intel import _classify, write_pool
from genomeqc_agent.evolution.regression_pool import RegressionPool

BASE_METRICS = {"judgment_pass_rate": 0.85, "pointer_resolution_rate": 0.99,
                "critical_false_pass": 0, "denominator": 40}


# -- M1: attribution ----------------------------------------------------------

@pytest.mark.parametrize("error_text,expected", [
    (("planner failed (no paid retry): gateway: output truncated "
      "(finish_reason=length, completion_tokens=4096)"), "infrastructure_error"),
    ("gateway: transport failure: ReadTimeout", "infrastructure_error"),
    ("gateway: provider returned 503: System is too busy now", "infrastructure_error"),
    ("gateway: provider returned 401: Token is invalid", "infrastructure_error"),
    ("planner failed (no paid retry): plan contract violation", "model_planning_error"),
    ("mystery failure", "unclassified"),
])
def test_classification_is_deterministic(error_text, expected):
    category, _mechanism = _classify(error_text)
    assert category == expected


def test_pool_is_append_only_and_idempotent(tmp_path):
    entries = [{"case_id": "case_a", "run_id": "r1", "kind": "failure",
                "category": "infrastructure_error",
                "error_evidence": {"reason": "x"}}]
    report_a = write_pool(entries, [{"counts": {"failures": 1}}], tmp_path)
    report_b = write_pool(entries, [{"counts": {"failures": 1}}], tmp_path)
    lines = (tmp_path / "failure_cases.v1.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(lines) == 1  # identical input never duplicates
    assert report_a["entries_total"] == report_b["entries_total"] == 1


# -- M2: proposer --------------------------------------------------------------

def test_proposer_generates_whitelisted_candidate():
    proposer = StrategyProposer(active_config={"max_tokens": 4096,
                                               "gateway_timeout_s": 60})
    entries = [{"case_id": "c1", "category": "infrastructure_error",
                "mechanism": "max_tokens too small for reasoning+content",
                "error_evidence": {"reason": "output truncated"}}]
    proposal = proposer.propose(entries)
    assert proposal["proposed"] is True
    candidate = proposal["candidates"][0]
    assert candidate["config_delta"] == {"max_tokens": 8192}  # whitelist only
    assert "c1" in candidate["failure_refs"]
    assert candidate["rollback_ref"]  # rollback reference recorded


def test_proposer_refuses_without_actionable_root_cause():
    proposer = StrategyProposer(active_config={"max_tokens": 8192,
                                               "gateway_timeout_s": 240})
    proposal = proposer.propose([])  # healthy pool
    assert proposal["proposed"] is False
    assert proposal["candidates"] == []
    assert "no candidate" in proposal["reason"]


def test_proposer_refuses_model_planning_errors():
    proposer = StrategyProposer(active_config={"max_tokens": 4096,
                                               "gateway_timeout_s": 120})
    entries = [{"case_id": "hallu", "category": "model_planning_error",
                "mechanism": "planner cited pointers failing mechanical resolution",
                "error_evidence": {}}]
    proposal = proposer.propose(entries)
    assert proposal["proposed"] is False
    assert any("human-authored prompt" in r for r in proposal["refusals"])


# -- M3: controller ------------------------------------------------------------

def _controller(tmp_path):
    return EvolutionController(tmp_path / "receipts",
                               active_config={"max_tokens": 4096})


def _candidate(controller, candidate_id="C1"):
    controller.propose(candidate_id, {
        "config_delta": {"max_tokens": 8192},
        "root_cause": "mock truncation family",
        "rollback_ref": json.dumps({"max_tokens": 4096}, sort_keys=True),
        "simulation": "offline_simulation",
    })


def test_state_machine_happy_path_publishes(tmp_path):
    controller = _controller(tmp_path)
    _candidate(controller)
    controller.transition("C1", "validated", metrics={
        "judgment_pass_rate": 0.9, "pointer_resolution_rate": 1.0,
        "critical_false_pass": 0, "denominator": 40}, baseline=BASE_METRICS)
    controller.transition("C1", "shadow_tested",
                          shadow_result={"replay": "historical"})
    controller.transition("C1", "accepted")
    controller.record_approval("C1", approver="human:maintainer",
                               decision="approve", real=True)
    controller.transition("C1", "approved", human_approval=True)
    controller.transition("C1", "published")
    assert controller.state["C1"]["status"] == "published"
    assert controller.active_config["max_tokens"] == 8192  # production updated


def test_human_approval_blocks_publishing(tmp_path):
    controller = _controller(tmp_path)
    _candidate(controller)
    controller.transition("C1", "validated", metrics={
        "judgment_pass_rate": 0.9, "pointer_resolution_rate": 1.0,
        "critical_false_pass": 0, "denominator": 40}, baseline=BASE_METRICS)
    controller.transition("C1", "shadow_tested")
    controller.transition("C1", "accepted")
    with pytest.raises(EvolutionError, match="human approval required"):
        controller.transition("C1", "approved", human_approval=False)
    assert controller.state["C1"]["status"] == "accepted"  # stuck, not published
    assert controller.active_config["max_tokens"] == 4096  # production untouched


def test_quality_gate_auto_rejects_regression(tmp_path):
    controller = _controller(tmp_path)
    _candidate(controller)
    candidate = controller.transition("C1", "validated", metrics={
        "judgment_pass_rate": 0.60, "pointer_resolution_rate": 0.95,
        "critical_false_pass": 0, "denominator": 40}, baseline=BASE_METRICS)
    assert candidate["status"] == "rejected"
    assert "regressed" in candidate["rejection_reason"]
    assert controller.active_config["max_tokens"] == 4096  # production unchanged


def test_critical_false_pass_auto_rejects(tmp_path):
    controller = _controller(tmp_path)
    _candidate(controller)
    candidate = controller.transition("C1", "validated", metrics={
        "judgment_pass_rate": 1.0, "pointer_resolution_rate": 1.0,
        "critical_false_pass": 2, "denominator": 40}, baseline=BASE_METRICS)
    assert candidate["status"] == "rejected"


def test_incomplete_denominator_rejects(tmp_path):
    controller = _controller(tmp_path)
    _candidate(controller)
    candidate = controller.transition("C1", "validated", metrics={
        "judgment_pass_rate": 0.9, "pointer_resolution_rate": 1.0,
        "critical_false_pass": 0, "denominator": 0}, baseline=BASE_METRICS)
    assert candidate["status"] == "rejected"


def test_illegal_transition_rejected(tmp_path):
    controller = _controller(tmp_path)
    _candidate(controller)
    with pytest.raises(EvolutionError, match="illegal transition"):
        controller.transition("C1", "published")  # proposed -> published


def test_receipts_written_per_transition(tmp_path):
    controller = _controller(tmp_path)
    _candidate(controller)
    controller.transition("C1", "validated", metrics={
        "judgment_pass_rate": 0.9, "pointer_resolution_rate": 1.0,
        "critical_false_pass": 0, "denominator": 40}, baseline=BASE_METRICS)
    receipt = tmp_path / "receipts" / "C1" / "validated.json"
    data = json.loads(receipt.read_text(encoding="utf-8"))
    assert data["simulation"] == "offline_simulation"
    assert data["transition"] == "validated"


# -- M4: regression memory -----------------------------------------------------

V2_POOL = {"pool": "v2", "entries": [
    {"id": "E2-2026-002", "family": "evidence.empty_array_pass",
     "status": "converted_to_regression",
     "regression_test": "bench/organelle_v2/tests/test_scoring_contract.py::x",
     "transition_note": "n"},
    {"id": "E2-2026-006", "family": "reasoning.count_hallucination",
     "status": "confirmed", "regression_test": None, "transition_note": "deferred"},
]}


def test_v2_pool_import_preserves_governance(tmp_path):
    v2_file = tmp_path / "v2_pool.json"
    v2_file.write_text(json.dumps(V2_POOL), encoding="utf-8")
    pool = RegressionPool(tmp_path / "pool.jsonl")
    imported = pool.import_v2_pool(v2_file)
    assert imported == 2
    formal = [e for e in pool.entries.values() if e["status"] == "formal_regression"]
    assert len(formal) == 1
    assert formal[0]["entry_id"] == "V3-E2-2026-002"
    assert formal[0]["review_status"].startswith("human_confirmed")
    pool.save()
    assert (tmp_path / "pool.jsonl").is_file()


def test_dedup_on_source_mechanism_scope(tmp_path):
    pool = RegressionPool(tmp_path / "pool.jsonl")
    first = pool.add_v3_case("fam", "mech", "scope", evidence={})
    second = pool.add_v3_case("fam", "mech", "scope", evidence={})
    assert first and second == ""  # second is a duplicate
    assert len(pool.entries) == 1


def test_status_transitions_governed(tmp_path):
    pool = RegressionPool(tmp_path / "pool.jsonl")
    entry_id = pool.add_v3_case("fam", "mech", "scope", evidence={})
    pool.set_status(entry_id, "human_approved", reviewer="user")
    pool.set_status(entry_id, "formal_regression", reviewer="user")
    assert pool.entries[entry_id]["status"] == "formal_regression"
    with pytest.raises(ValueError):
        pool.set_status(entry_id, "bogus", reviewer="user")


# -- M4 formal regressions over real behaviour ---------------------------------

def test_hallucinated_pointer_downgrades_validity():
    """E2 formal regression: unresolved planner-cited pointers must downgrade
    evidence_validity (interception) without hiding the QC decision."""
    from genomeqc_agent.reports.composer import compose_report
    report = compose_report(
        run_id="r", packet_meta={"packet_id": "p"}, domain="chloroplast",
        model_id="m", tool_results=[],
        facts={"coordinate_violations": [{"feature_id": "CDS_003",
                                          "pointer": "artifacts/features.tsv:13"}],
               "multipart_structure_issues": [],
               "cds_protein_ids_without_fasta_record": [],
               "fasta_protein_ids_without_cds": [],
               "statistics": {"coordinate_system": {
                   "equal": True,
                   "pointer": "artifacts/sequence_stats.json:coordinate_system"}}},
        unresolved_count=2,
        tool_calls={}, versions={})
    assert report.qc_decision == "block"          # real defect still surfaces
    assert report.evidence_validity == "partial"  # hallucinated refs downgraded


def test_truncation_is_attributed():
    """E2 formal regression: truncation maps to infrastructure_error."""
    from genomeqc_agent.evolution.failure_intel import _classify
    category, mechanism = _classify(
        "gateway: output truncated (finish_reason=length, completion_tokens=4096)")
    assert category == "infrastructure_error"
    assert "max_tokens" in mechanism


def test_null_tool_name_normalised():
    """E2 formal regression: model null tool_name is contract-normalised."""
    from genomeqc_agent.model.scripted_planner import PlannedAction
    action = PlannedAction.model_validate(
        {"kind": "synthesize", "tool_name": None, "arguments": {}, "reason": "done"})
    assert action.tool_name == ""
