"""Packet intake: layout validation, identity hashing, GenBank conversion.

The public view of every packet is exactly ``task.md`` + ``artifacts/*``.
Anything else in the directory (provenance.json, canonical.gbff, private/)
never enters the tool-visible snapshot.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from genomeqc_agent.adapters.v2_adapter import PacketFiles, SnapshotV2Port
from genomeqc_agent.adapters.v2_snapshot.genbank import parse_genbank
from genomeqc_agent.adapters.v2_snapshot.v2_common import canonical
from genomeqc_agent.config import REQUIRED_ARTIFACTS
from genomeqc_agent.schemas.packet import PacketMeta

PUBLIC_VIEW_ENTRIES = ("task.md", "artifacts")


class PacketIntakeError(ValueError):
    """Raised when a submission cannot become a valid public packet."""


def public_view_files(packet_dir: Path) -> list[Path]:
    """Whitelisted public files of a packet, mirroring V2 visible_files rules."""
    packet_dir = Path(packet_dir).resolve(strict=True)
    task, artifacts = packet_dir / "task.md", packet_dir / "artifacts"
    if task.is_symlink() or artifacts.is_symlink() or not task.is_file() or not artifacts.is_dir():
        raise PacketIntakeError("packet must contain a regular task.md and an artifacts directory")
    paths = [task]
    for path in sorted(artifacts.rglob("*")):
        if path.is_symlink():
            raise PacketIntakeError("packet artifacts cannot contain symlinks")
        if path.is_file():
            if artifacts not in path.resolve().parents:
                raise PacketIntakeError("artifact leaves the public view")
            # Blacklist mirrors the V2 isolation matrix
            # (tests/test_no_answer_leakage.py::FORBIDDEN_ARTIFACT_NAMES).
            if path.name.lower() in {"meta.json", "expected.json", "rubric.json",
                                     "mutation.json", "oracle_report.json"}:
                raise PacketIntakeError("private filename inside artifacts")
            paths.append(path)
    missing = [name for name in REQUIRED_ARTIFACTS if not (artifacts / name).is_file()]
    if missing:
        raise PacketIntakeError(f"artifacts missing required files: {missing}")
    return paths


def packet_identity(packet_id: str, packet_dir: Path, domain: str) -> PacketMeta:
    """Content hash over the public view only (task.md + artifacts/*)."""
    paths = public_view_files(packet_dir)
    root = Path(packet_dir).resolve()
    mapping = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in paths}
    input_sha256 = hashlib.sha256(canonical(mapping)).hexdigest()
    return PacketMeta(
        packet_id=packet_id,
        packet_dir=str(root),
        input_sha256=input_sha256,
        domain=domain,  # type: ignore[arg-type]
        task_md_sha256=mapping.get("task.md"),
        artifact_files=[name for name in mapping if name.startswith("artifacts/")],
    )


def convert_genbank_upload(raw_bytes: bytes, out_dir: Path, packet_id: str) -> PacketMeta:
    """Convert one GenBank upload into the case-layout packet, public view only.

    Uses the V2 snapshot converter; the private provenance map stays out of
    the public view (it is not written into out_dir at all in G1).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PacketIntakeError("upload is not valid UTF-8 text") from error
    try:
        record = parse_genbank(text)
        packet = SnapshotV2Port().build_packet(record, raw_bytes)
    except ValueError as error:
        raise PacketIntakeError(f"genbank record rejected: {error}") from error
    artifacts = out_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "genome.fa").write_bytes(packet["genome_fa"])
    (artifacts / "features.tsv").write_bytes(packet["features_tsv"])
    (artifacts / "proteins.faa").write_bytes(packet["proteins_faa"])
    (artifacts / "sequence_stats.json").write_bytes(canonical(packet["sequence_stats"]))
    task_md = (
        "# Organelle genome delivery QC review\n\n"
        "Converted from an uploaded GenBank record. Verify the internal\n"
        "consistency of artifacts/ using only these files.\n"
    )
    (out_dir / "task.md").write_text(task_md, encoding="utf-8")
    return packet_identity(packet_id, out_dir, "auto_detect_supported")


def open_packet_files(packet_dir: Path) -> tuple[PacketMeta, PacketFiles, dict]:
    """Validate + open a packet; returns (meta, tool-visible snapshot, identity dict)."""
    packet_dir = Path(packet_dir)
    # packet_id derives from the directory name; intake callers pass explicit ids.
    meta = packet_identity(packet_dir.name, packet_dir, "auto_detect_supported")
    files = SnapshotV2Port().open_packet(packet_dir)
    return meta, files, json.loads(meta.model_dump_json())
