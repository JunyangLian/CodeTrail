"""Tool contract behaviour: deterministic facts, pointer syntax, error codes."""
from __future__ import annotations

from pathlib import Path

import pytest

from genomeqc_agent.jobs.smoke_pipeline import default_registry
from genomeqc_agent.schemas.tool import ToolRequest

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "public_packets"
PACKET = EXAMPLES / "chloro_C1_NC_013991_2_CHLORO-CDS-03"  # public fault view


@pytest.fixture()
def registry():
    reg = default_registry()
    reg.register_packet("demo", PACKET)
    return reg


def _call(registry, tool, arguments, packet_id="demo"):
    return registry.execute(
        ToolRequest(run_id="aaaaaaaa-0000-0000-0000-000000000001",
                    tool_name=tool, arguments=arguments), packet_id)


def test_inspect_packet_lists_public_files(registry):
    result = _call(registry, "inspect_packet", {})
    assert result.ok
    paths = {entry["path"] for entry in result.result["manifest"]}
    assert {"task.md", "artifacts/genome.fa", "artifacts/features.tsv",
            "artifacts/proteins.faa", "artifacts/sequence_stats.json"} <= paths
    assert all(len(entry["sha256"]) == 64 for entry in result.result["manifest"])


def test_organelle_summary_contract(registry):
    result = _call(registry, "organelle_summary", {})
    assert result.ok
    facts = result.result
    assert facts["facts_version"] == "organelle-public-facts/v1"
    assert "coordinate_system" in facts["statistics"]
    assert result.versions.v2_adapter_version.startswith("v2.1.1-snapshot:")


def test_lookup_feature_returns_pointers(registry):
    result = _call(registry, "lookup_feature", {"feature_id": "CDS_001"})
    assert result.ok
    assert result.result["row_count"] >= 1
    assert all(row["pointer"].startswith("artifacts/features.tsv:")
               for row in result.result["rows"])


def test_lookup_feature_unknown_id_is_empty_not_error(registry):
    result = _call(registry, "lookup_feature", {"feature_id": "NOPE_999"})
    assert result.ok and result.result["row_count"] == 0


def test_verify_pointer_kinds(registry):
    result = _call(registry, "verify_evidence_pointer", {"pointers": [
        "artifacts/sequence_stats.json:sequence_length",   # JSON top-level key
        "artifacts/features.tsv:1",                        # 1-based line
        "artifacts/features.tsv:999999",                   # out-of-range line
        "artifacts/missing.json:key",                      # missing file
        "no-colon-pointer",                                # malformed
    ]})
    assert result.ok
    by_pointer = {p["pointer"]: p for p in result.result["pointers"]}
    assert by_pointer["artifacts/sequence_stats.json:sequence_length"]["resolved"]
    assert by_pointer["artifacts/features.tsv:1"]["locator_kind"] == "numbered_line"
    assert not by_pointer["artifacts/features.tsv:999999"]["resolved"]
    assert by_pointer["artifacts/missing.json:key"]["locator_kind"] == "missing_file"
    assert by_pointer["no-colon-pointer"]["locator_kind"] == "malformed_pointer"
    assert set(result.result["unresolved"]) == {
        "artifacts/features.tsv:999999", "artifacts/missing.json:key", "no-colon-pointer"}


def test_verify_pointer_rejects_paths_outside_whitelist(registry):
    result = _call(registry, "verify_evidence_pointer", {
        "pointers": ["../../../../etc/passwd:x"]})
    assert not result.ok
    assert result.error.error_code == "path_denied"


def test_version_info_shape(registry):
    result = _call(registry, "inspect_packet", {})
    versions = result.versions.model_dump()
    assert versions["tool_contract_version"] == "1"
    assert len(versions["input_schema_hash"]) == 64
