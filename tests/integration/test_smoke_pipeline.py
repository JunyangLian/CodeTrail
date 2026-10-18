"""End-to-end offline pipeline on the exported public demo packets."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from genomeqc_agent.jobs.smoke_pipeline import default_registry, run_smoke

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "public_packets"
NORMAL_MITO = EXAMPLES / "mito_M1_NC_002333_2_MITO-NORMAL-00"
NORMAL_CHLORO = EXAMPLES / "chloro_C1_NC_013991_2_CHLORO-NORMAL-00"
FAULT_CHLORO = EXAMPLES / "chloro_C1_NC_013991_2_CHLORO-CDS-03"


def test_normal_packets_pass_with_verified_facts(tmp_path):
    for packet in (NORMAL_MITO, NORMAL_CHLORO):
        outcome = run_smoke(packet, out_dir=tmp_path, registry=default_registry())
        report = outcome["report"]
        assert report["qc_decision"] == "pass"
        assert report["evidence_validity"] == "valid"
        assert all(f["status"] == "pass" for f in report["findings"])
        assert outcome["model_requests"] >= 1  # FakeModel planned the run
        assert outcome["receipt"]["unresolved_pointers"] == []


def test_fault_packet_blocks_with_traceable_evidence(tmp_path):
    outcome = run_smoke(FAULT_CHLORO, out_dir=tmp_path, registry=default_registry())
    report = outcome["report"]
    assert report["qc_decision"] == "block"
    blocked = [f for f in report["findings"] if f["status"] == "block"]
    assert blocked, "the injected public fault must surface as a block finding"
    for finding in blocked:
        assert finding["evidence_refs"], "block findings must cite evidence pointers"
        assert all(ref.startswith("artifacts/") for ref in finding["evidence_refs"])
    assert report["evidence_validity"] == "valid"
    # dynamic branch: multipart issues trigger a lookup_feature call
    if any(f["check_id"] == "multipart_structure" and f["status"] == "block"
           for f in report["findings"]):
        assert report["tool_calls"].get("lookup_feature", 0) >= 1


def test_report_files_written_and_receipt_hashed(tmp_path):
    run_id = "0f0e0d0c-0b0a-49f8-8c7d-665544332211"
    outcome = run_smoke(NORMAL_CHLORO, out_dir=tmp_path, run_id=run_id,
                        registry=default_registry())
    written = outcome["written"]
    report_json = Path(written["qc_report_json"]).read_bytes()
    receipt = json.loads(Path(written["run_receipt"]).read_text(encoding="utf-8"))
    import hashlib
    assert hashlib.sha256(report_json).hexdigest() == receipt["report_sha256"]
    markdown = Path(written["qc_report_md"]).read_text(encoding="utf-8")
    assert f"# QC Report {run_id}" in markdown
    assert "## Not checked" in markdown


def test_not_checked_honestly_listed(tmp_path):
    outcome = run_smoke(NORMAL_CHLORO, registry=default_registry())
    assert any("translation" in item for item in outcome["report"]["not_checked"])


def test_repeat_submission_is_idempotent(tmp_path):
    first = run_smoke(NORMAL_CHLORO, run_id="11111111-2222-4333-8444-555555555555",
                      registry=default_registry())
    reg = default_registry()
    reg.register_packet(first["report"]["packet"]["packet_id"],
                        NORMAL_CHLORO)  # pre-registered: second run must not fail
    second = run_smoke(NORMAL_CHLORO, run_id="11111111-2222-4333-8444-555555555555",
                       registry=reg)
    assert second["report"]["packet"]["input_sha256"] == \
        first["report"]["packet"]["input_sha256"]


@pytest.mark.parametrize("packet", [
    NORMAL_MITO, NORMAL_CHLORO, FAULT_CHLORO,
], ids=["mito-normal", "chloro-normal", "chloro-fault"])
def test_three_demo_packets_complete(packet, tmp_path):
    outcome = run_smoke(packet, out_dir=tmp_path, registry=default_registry())
    assert outcome["report"]["findings"]
    assert Path(outcome["written"]["qc_report_json"]).is_file()
