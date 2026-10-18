"""P1.2/P1.3 data flywheel + evolution loop tests (offline).

P2 checklist coverage: flywheel ingestion, dedup, source isolation
(user uploads never auto-enter), review identity rules, and the FULL
self-improvement chain over real historical traces.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from genomeqc_agent.evolution.evolution_loop import run_loop
from genomeqc_agent.evolution.flywheel import DataFlywheel, source_group_of
from genomeqc_agent.evolution.regression_pool import RegressionPool

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_POOL = ROOT / "evolution_state" / "failure_pool" / "failure_cases.v1.jsonl"


def _failed_state(packet_id: str, run_id: str, error: str) -> dict:
    return {"run_id": run_id, "input": {"packet_id": packet_id,
                                        "input_sha256": "deadbeef" * 8},
            "runtime": {"status": "failed", "last_error": error}}


def _partial_state(packet_id: str, run_id: str) -> dict:
    return {"run_id": run_id,
            "input": {"packet_id": packet_id, "input_sha256": "cafebabe" * 8},
            "runtime": {"status": "succeeded"},
            "strategy": {"prompt_version": "promptv2"},
            "evidence": {"unresolved": ["artifacts/proteins.fa:all_sequences"]},
            "report": {"qc_decision": "block", "evidence_validity": "partial"}}


def _flywheel(tmp_path, **kwargs) -> DataFlywheel:
    return DataFlywheel(RegressionPool(tmp_path / "pool.jsonl"),
                        tmp_path / "flywheel", **kwargs)


# -- flywheel -------------------------------------------------------------------

def test_ingest_extracts_failures_and_high_risk_with_full_provenance(tmp_path):
    fw = _flywheel(tmp_path)
    report = fw.ingest_run_states([
        _failed_state("chloro_C1_NC_013991_2_CHLORO-CDS-03", "r1",
                      "gateway: provider returned 503: busy"),
        _partial_state("chloro_C1_NC_088770_1_CHLORO-CDS-03", "r2"),
    ], source="g4_historical_trace")
    assert report["ingested"] == 2
    cases = fw.pending_cases()
    assert {c["source_group_id"] for c in cases} == {"NC_013991_2", "NC_088770_1"}
    by_id = {c["run_id"]: c for c in cases}
    assert by_id["r1"]["category"] == "infrastructure_error"
    assert by_id["r2"]["category"] == "high_risk_success"
    assert by_id["r2"]["input_sha256"] == "cafebabe" * 8  # source hash kept
    assert by_id["r2"]["prompt_version"] == "promptv2"    # version kept
    assert by_id["r2"]["status"] == "pending_review"       # human review pending


def test_dedup_on_source_mechanism_scope_across_runs(tmp_path):
    fw = _flywheel(tmp_path)
    first = fw.ingest_run_states(
        [_failed_state("chloro_C1_NC_013991_2_A", "r1", "gateway: 503 busy")],
        source="g4_historical_trace")
    second = fw.ingest_run_states(
        [_failed_state("chloro_C1_NC_013991_2_B", "r2", "gateway: 503 busy")],
        source="g4_historical_trace")
    assert first["ingested"] == 1 and second["ingested"] == 0
    assert second["skipped_duplicate"] == 1


def test_user_uploads_excluded_by_default(tmp_path):
    fw = _flywheel(tmp_path)
    report = fw.ingest_run_states(
        [_failed_state("upload_ab12cd34", "u1", "gateway: 503 busy")],
        source="user_upload")
    assert report["ingested"] == 0  # default: uploads never enter the pool
    fw_open = _flywheel(tmp_path / "open", allow_user_uploads=True)
    assert fw_open.ingest_run_states(
        [_failed_state("upload_ab12cd34", "u1", "gateway: 503 busy")],
        source="user_upload")["ingested"] == 1  # explicit operator opt-in only


def test_review_identity_rules(tmp_path):
    fw = _flywheel(tmp_path)
    fw.ingest_run_states(
        [_failed_state("chloro_C1_NC_013991_2_A", "r1", "gateway: 503 busy")],
        source="g4_historical_trace")
    case_id = fw.pending_cases()[0]["case_id"]
    # simulated reviewer: entry lands in the pool but stays a candidate
    receipt = fw.review(case_id, reviewer="offline_simulation:driver",
                        decision="approve")
    assert receipt["simulated"] is True
    entry = fw.pool.entries[receipt["pool_entry_id"]]
    assert entry["status"] == "candidate"  # simulated review cannot promote
    assert entry["evidence"]["reviewed_by"] == "offline_simulation:driver"
    # a REAL reviewer promotes to human_approved
    fw2 = _flywheel(tmp_path / "real")
    fw2.ingest_run_states(
        [_failed_state("chloro_C1_NC_013991_2_A", "r1", "gateway: 503 busy")],
        source="g4_historical_trace")
    receipt2 = fw2.review(fw2.pending_cases()[0]["case_id"],
                          reviewer="human:curator", decision="approve")
    entry2 = fw2.pool.entries[receipt2["pool_entry_id"]]
    assert entry2["status"] == "human_approved"
    assert entry2["review_status"] == "human_approved by human:curator"


def test_review_reject_leaves_pool_untouched(tmp_path):
    fw = _flywheel(tmp_path)
    fw.ingest_run_states(
        [_failed_state("chloro_C1_NC_013991_2_A", "r1", "gateway: 503 busy")],
        source="g4_historical_trace")
    case_id = fw.pending_cases()[0]["case_id"]
    receipt = fw.review(case_id, reviewer="human:curator", decision="reject")
    assert receipt["pool_entry_id"] == ""
    assert fw.pool.entries == {}


def test_source_group_extraction():
    assert source_group_of("chloro_C1_NC_013991_2_CHLORO-NORMAL-00") == "NC_013991_2"
    assert source_group_of("mito_M1_NC_002333_2_MITO-NORMAL-00") == "NC_002333_2"


# -- feature flags ----------------------------------------------------------------

def test_feature_flags_default_off_and_safety_rails():
    from genomeqc_agent.evolution import flags
    snapshot = flags.get_flags()
    assert snapshot["self_refinement"] is False
    assert snapshot["data_flywheel_auto_ingest"] is False
    assert snapshot["shadow_auto_promote"] is False
    with pytest.raises(ValueError, match="permanently disabled"):
        flags.set_flag("shadow_auto_promote", True)
    with pytest.raises(KeyError):
        flags.set_flag("nonexistent_flag", True)
    flags.set_flag("self_refinement", True)
    assert flags.is_enabled("self_refinement") is True
    flags.set_flag("self_refinement", False)  # restore
    assert flags.get_flags() == snapshot


# -- evolution loop (real historical traces) --------------------------------------

@pytest.fixture()
def historical_entries() -> list[dict]:
    return [json.loads(line) for line in
            HISTORICAL_POOL.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_full_chain_over_real_historical_traces(tmp_path, historical_entries):
    assert len(historical_entries) >= 5  # real pool, not a fixture stub
    pool_path = tmp_path / "e2_pool.v1.jsonl"
    pool = RegressionPool(pool_path)
    pool.add_v3_case("loop.gate.check", "loop gate check", "any",
                     evidence={}, status="formal_regression",
                     regression_test=("tests/unit/test_refinement.py::"
                                      "test_locator_miss_is_never_corrected"))
    pool.save()
    report = run_loop(historical_entries, tmp_path / "loop",
                      active_config={"max_tokens": 8192},
                      tests_root=ROOT, pool_path=pool_path)
    stages = report["stages"]
    # regression gate executed the real deterministic regression
    assert stages["regression_gate"]["verdict"] == "PASS"
    assert stages["regression_gate"]["executed"] >= 1
    # flywheel ingested real history; simulated review keeps entry at candidate
    assert stages["data_flywheel"]["ingested"] >= 1
    assert stages["data_flywheel"]["simulated_review_promoted"] is False
    assert stages["data_flywheel"]["pool_status_of_reviewed"] == "candidate"
    # failure analyzer + proposer
    assert stages["failure_analyzer"]["entries"] == len(historical_entries)
    assert stages["strategy_proposer"]["behavior_policy_proposed"] is True
    assert stages["strategy_proposer"]["status_cap"] == "validated"
    # shadow skipped honestly (no runner) + promotion gate blocked
    assert stages["shadow_ab"]["status"] == "skipped"
    assert stages["promotion_gate"]["simulated_approval_blocked_publishing"] is True
    assert stages["promotion_gate"]["active_strategy_unchanged"] is True
    assert stages["promotion_gate"]["capped_candidate_untouched"] is True
    # report file written
    assert (tmp_path / "loop" / "evolution_loop_report.v1.json").is_file()
