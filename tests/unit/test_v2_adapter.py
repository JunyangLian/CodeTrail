"""V2Port adapter + GenBank conversion on the packet snapshot."""
from __future__ import annotations

from pathlib import Path

import pytest

from genomeqc_agent.adapters.packet_adapter import (
    PacketIntakeError,
    convert_genbank_upload,
    packet_identity,
)
from genomeqc_agent.adapters.v2_adapter import PacketFiles, SnapshotV2Port, V2Port
from genomeqc_agent.config import V2_ADAPTER_VERSION

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "public_packets"
NORMAL_CHLORO = EXAMPLES / "chloro_C1_NC_013991_2_CHLORO-NORMAL-00"

MINIMAL_GBFF = """LOCUS       TESTD                    60 bp ds-DNA circular SYN 17-OCT-2026
DEFINITION  Test record for V3 adapter.
ACCESSION   TESTD
VERSION     TESTD.1
KEYWORDS    .
SOURCE      Test organism
  ORGANISM  Test organism
            Test phylum
FEATURES             Location/Qualifiers
     source          1..60
                     /organism="Test organism"
     CDS             join(1..30,31..60)
                     /gene=atpA
                     /protein_id="TST_1"
                     /translation="MKVL"
ORIGIN
        1 acgtacgtac gtacgtacgt acgtacgtac gtacgtacgt acgtacgtac gtacgtacgt
//
"""


def test_v2port_protocol_satisfied():
    port = SnapshotV2Port()
    assert isinstance(port, V2Port)
    assert port.adapter_version == V2_ADAPTER_VERSION


def test_open_packet_snapshot_is_immutable_and_whitelisted():
    files = SnapshotV2Port().open_packet(NORMAL_CHLORO)
    assert isinstance(files, PacketFiles)
    paths = [entry["path"] for entry in files.manifest()]
    assert "task.md" in paths
    assert "artifacts/genome.fa" in paths
    with pytest.raises(ValueError):
        files.text("../../../etc/passwd")
    with pytest.raises(ValueError):
        files.text("artifacts/genome.fa/../../provenance.json")


def test_public_facts_on_normal_packet():
    files = SnapshotV2Port().open_packet(NORMAL_CHLORO)
    facts = SnapshotV2Port().public_facts(files)
    assert facts["facts_version"] == "organelle-public-facts/v1"
    assert facts["coordinate_violations"] == []
    assert facts["multipart_structure_issues"] == []
    assert facts["cds_protein_ids_without_fasta_record"] == []
    assert facts["fasta_protein_ids_without_cds"] == []
    assert all(pair["equal"] for pair in facts["statistics"].values())


def test_packet_identity_is_content_hashed():
    meta_a = packet_identity("demo", NORMAL_CHLORO, "chloroplast")
    meta_b = packet_identity("demo", NORMAL_CHLORO, "chloroplast")
    assert meta_a.input_sha256 == meta_b.input_sha256
    assert len(meta_a.input_sha256) == 64


def test_convert_genbank_upload_round_trip(tmp_path):
    out = tmp_path / "TESTD.1"
    meta = convert_genbank_upload(MINIMAL_GBFF.encode("utf-8"), out, "TESTD_1")
    assert meta.artifact_files == [
        "artifacts/features.tsv", "artifacts/genome.fa",
        "artifacts/proteins.faa", "artifacts/sequence_stats.json",
    ]
    files = SnapshotV2Port().open_packet(out)
    facts = SnapshotV2Port().public_facts(files)
    # synthetic record: 2-part CDS, no faults
    assert facts["multipart_feature_count"] == 1
    assert facts["coordinate_violations"] == []
    assert facts["statistics"]["protein_count"]["equal"] is True
    # provenance must not leak into the public view
    assert not (out / "provenance.json").exists()


def test_convert_rejects_broken_genbank(tmp_path):
    with pytest.raises(PacketIntakeError):
        convert_genbank_upload(b"not a genbank record", tmp_path, "bad1")
