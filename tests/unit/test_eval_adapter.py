"""Eval adapter: deterministic QCReport -> V2 contract mapping."""
from __future__ import annotations

import pytest

from genomeqc_agent.eval.adapter import map_report


def _report(verdict="pass", findings=None):
    return {"qc_decision": verdict,
            "evidence_validity": "valid",
            "findings": findings or [
                {"check_id": "coordinate_bounds", "status": "pass", "summary": "ok",
                 "evidence_refs": ["artifacts/sequence_stats.json:sequence_length"]},
            ]}


def test_pass_maps_to_contract_with_stats_evidence():
    mapped = map_report(_report())
    assert mapped["mappable"] is True
    out = mapped["output"]
    assert out["verdict"] == "pass"
    assert out["root_cause"] == "none"
    assert out["evidence"][0]["pointer"] == "artifacts/sequence_stats.json:sequence_length"
    assert out["action"]


def test_coordinate_block_maps_to_coordinate_out_of_bounds():
    findings = [
        {"check_id": "coordinate_bounds", "status": "block", "summary": "row outside",
         "evidence_refs": ["artifacts/features.tsv:13"]},
        {"check_id": "stats_consistency", "status": "pass", "summary": "ok",
         "evidence_refs": ["artifacts/sequence_stats.json:sequence_length"]},
    ]
    mapped = map_report(_report("block", findings))
    assert mapped["mappable"] is True
    out = mapped["output"]
    assert out["verdict"] == "block"
    assert out["root_cause"] == "coordinate_out_of_bounds"
    assert out["evidence"][0] == {"pointer": "artifacts/features.tsv:13",
                                  "note": "coordinate_bounds"}
    assert "coordinate_bounds" in out["observed_defect"]


@pytest.mark.parametrize("check_id,expected_cause", [
    ("cross_file_protein_id", "cross_file_id_mismatch"),
    ("stats_consistency", "stats_conflict"),
])
def test_root_cause_mapping_table(check_id, expected_cause):
    findings = [{"check_id": check_id, "status": "block", "summary": "s",
                 "evidence_refs": ["artifacts/features.tsv:2"]}]
    mapped = map_report(_report("block", findings))
    assert mapped["output"]["root_cause"] == expected_cause


def test_insufficient_evidence_is_not_mappable():
    mapped = map_report(_report("insufficient_evidence"))
    assert mapped["mappable"] is False
    assert "no honest" in mapped["reason"]


def test_multipart_block_has_no_v2_token():
    findings = [{"check_id": "multipart_structure", "status": "block", "summary": "s",
                 "evidence_refs": ["artifacts/features.tsv:5"]}]
    mapped = map_report(_report("block", findings))
    assert mapped["mappable"] is False
    assert "multipart_structure" in mapped["reason"]


def test_multi_block_spans_are_not_mappable():
    findings = [
        {"check_id": "coordinate_bounds", "status": "block", "summary": "s1",
         "evidence_refs": ["artifacts/features.tsv:13"]},
        {"check_id": "cross_file_protein_id", "status": "block", "summary": "s2",
         "evidence_refs": ["artifacts/features.tsv:20"]},
    ]
    mapped = map_report(_report("block", findings))
    assert mapped["mappable"] is False
    assert "multi-block" in mapped["reason"]


def test_no_evidence_is_not_mappable():
    mapped = map_report({"qc_decision": "pass", "findings": [
        {"check_id": "stats_consistency", "status": "pass", "summary": "ok",
         "evidence_refs": []}]})
    assert mapped["mappable"] is False
