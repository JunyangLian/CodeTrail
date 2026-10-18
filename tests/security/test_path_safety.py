"""Security: forged paths, private files, oversized packets, budget, unknown tools."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from genomeqc_agent.jobs.smoke_pipeline import default_registry
from genomeqc_agent.schemas.tool import ToolRequest
from genomeqc_agent.tools.lookup_feature import LookupFeatureArgs
from genomeqc_agent.tools.verify_evidence import VerifyEvidenceArgs

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "public_packets"
PACKET = EXAMPLES / "chloro_C1_NC_013991_2_CHLORO-NORMAL-00"


def _execute(registry, tool: str, arguments: dict, packet_id: str = "demo"):
    return registry.execute(
        ToolRequest(run_id="11111111-1111-1111-1111-111111111111",
                    tool_name=tool, arguments=arguments), packet_id)


@pytest.fixture()
def registry():
    reg = default_registry()
    reg.register_packet("demo", PACKET)
    return reg


# -- filesystem escape attempts against the snapshot --------------------------

@pytest.mark.parametrize("path", [
    "../provenance.json",
    "../../V3_MASTER_PLAN.md",
    "/etc/passwd",
    "C:\\Windows\\system32\\config",
    "artifacts/../../../secret.txt",
    "artifacts\\genome.fa",
    "artifacts/genome.fa\x00.txt",
    "artifacts/nonexistent.fa",
    "",
])
def test_forged_paths_are_denied(registry, path):
    files = registry.files_for("demo")
    with pytest.raises(ValueError):
        files.text(path)


def test_private_files_never_enter_public_view(tmp_path):
    """A packet directory carrying expected.json/rubric.json stays unreadable."""
    malicious = tmp_path / "evil_packet"
    (malicious / "artifacts").mkdir(parents=True)
    for name in ("genome.fa", "features.tsv", "proteins.faa", "sequence_stats.json"):
        (malicious / "artifacts" / name).write_text("placeholder\n", encoding="utf-8")
    (malicious / "artifacts" / "expected.json").write_text('{"verdict": "pass"}', encoding="utf-8")
    (malicious / "task.md").write_text("task\n", encoding="utf-8")
    with pytest.raises(ValueError):
        default_registry().register_packet("evil", malicious)


@pytest.mark.parametrize("private_name", ["expected.json", "rubric.json", "mutation.json",
                                          "meta.json", "oracle_report.json"])
def test_each_forbidden_artifact_name_is_rejected(tmp_path, private_name):
    """Every name in the V2 FORBIDDEN_ARTIFACT_NAMES matrix is refused."""
    packet = tmp_path / "p"
    (packet / "artifacts").mkdir(parents=True)
    for name in ("genome.fa", "features.tsv", "proteins.faa", "sequence_stats.json"):
        (packet / "artifacts" / name).write_text("placeholder\n", encoding="utf-8")
    (packet / "artifacts" / private_name).write_text("{}\n", encoding="utf-8")
    (packet / "task.md").write_text("task\n", encoding="utf-8")
    with pytest.raises(ValueError, match="private filename"):
        default_registry().register_packet("p", packet)


def test_symlink_in_artifacts_is_rejected(tmp_path):
    link_packet = tmp_path / "link_packet"
    (link_packet / "artifacts").mkdir(parents=True)
    (link_packet / "task.md").write_text("task\n", encoding="utf-8")
    target = tmp_path / "outside.fa"
    target.write_text(">x\nACGT\n", encoding="utf-8")
    os.symlink(target, link_packet / "artifacts" / "genome.fa")
    with pytest.raises(ValueError):
        default_registry().register_packet("link", link_packet)


def test_oversized_file_rejected(tmp_path):
    big = tmp_path / "big_packet"
    (big / "artifacts").mkdir(parents=True)
    (big / "task.md").write_text("task\n", encoding="utf-8")
    (big / "artifacts" / "genome.fa").write_bytes(b">x\n" + b"A" * 600_000 + b"\n")
    for name in ("features.tsv", "proteins.faa", "sequence_stats.json"):
        (big / "artifacts" / name).write_text("placeholder\n", encoding="utf-8")
    with pytest.raises(ValueError):
        default_registry().register_packet("big", big)


# -- registry-level safety -----------------------------------------------------

def test_unknown_tool_is_rejected(registry):
    result = _execute(registry, "run_shell", {"command": "rm -rf /"})
    assert not result.ok
    assert result.error.error_code == "schema_invalid"
    assert result.error.retryable is False


def test_extra_arguments_are_rejected(registry):
    result = _execute(registry, "lookup_feature",
                      {"feature_id": "CDS_001", "raw_sql": "DROP TABLE x"})
    assert not result.ok
    assert result.error.error_code == "schema_invalid"


def test_budget_exhaustion_stops_tool_calls(registry):
    small = default_registry()
    small.max_tool_calls = 1
    small.register_packet("demo", PACKET)
    first = _execute(small, "inspect_packet", {})
    assert first.ok
    second = _execute(small, "inspect_packet", {})
    assert not second.ok
    assert second.error.error_code == "budget_exceeded"


def test_unregistered_packet_rejected(registry):
    result = _execute(registry, "inspect_packet", {}, packet_id="ghost")
    assert not result.ok
    assert result.error.error_code == "packet_not_found"


def test_lookup_feature_args_validation():
    with pytest.raises(ValidationError):
        LookupFeatureArgs(feature_id="")
    with pytest.raises(ValidationError):
        LookupFeatureArgs(feature_id="x" * 200)


def test_verify_pointer_list_length_bound():
    with pytest.raises(ValidationError):
        VerifyEvidenceArgs(pointers=[f"artifacts/features.tsv:{i}" for i in range(51)])
    ok = VerifyEvidenceArgs(pointers=[f"artifacts/features.tsv:{i}" for i in range(50)])
    assert len(ok.pointers) == 50
