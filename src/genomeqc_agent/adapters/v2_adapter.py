"""V2Port: the ONLY seam between GenomeQC Agent and V2-derived code.

Business layers depend on this Protocol, never on V2 internals. The G1
implementation wraps the hash-anchored snapshot (adapters/v2_snapshot), so
the V2 repository stays untouched and unneeded at runtime.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from genomeqc_agent.adapters.v2_snapshot.facts import public_facts
from genomeqc_agent.adapters.v2_snapshot.genbank_to_packet import build_packet
from genomeqc_agent.adapters.v2_snapshot.readonly import PublicFiles
from genomeqc_agent.adapters.v2_snapshot.verify import _resolve
from genomeqc_agent.adapters.v2_snapshot.visible_input import visible_files
from genomeqc_agent.config import PACKET_FILE_LIMIT, PACKET_PACKET_LIMIT, V2_ADAPTER_VERSION


class PacketFiles(PublicFiles):
    """V2 PublicFiles with the audited V2 PublicFilesV2 size budget.

    Same whitelist semantics and per-read path validation as the snapshot;
    only the size guards widen (chloroplast genome.fa reaches ~160 KB, which
    the V1 128 KB limit rejects). Precedent: bench/organelle_v2/
    inspect_integration/readonly.py overrode __init__ exactly this way
    because the V1 constructor hardcodes its limit literals.
    """

    FILE_LIMIT = PACKET_FILE_LIMIT
    PACKET_LIMIT = PACKET_PACKET_LIMIT

    # V2's visible_files only rejects meta/expected by name (its generator
    # guarantees the rest); V3 must not trust the producer, so the full
    # FORBIDDEN_ARTIFACT_NAMES matrix from V2 tests/test_no_answer_leakage.py
    # is enforced here, at load time, inside the sandbox constructor.
    FORBIDDEN_NAMES = frozenset({"meta.json", "expected.json", "rubric.json",
                                 "mutation.json", "oracle_report.json"})

    def __init__(self, case):
        case = Path(case).resolve(strict=True)
        data = {}
        for path in visible_files(case):
            if path.name.lower() in self.FORBIDDEN_NAMES:
                raise ValueError("private filename inside the public view")
            content = path.read_bytes()
            if len(content) > self.FILE_LIMIT:
                raise ValueError("public file exceeds the packet size limit")
            content.decode("utf-8")  # plain text only, as in the V2 snapshot
            data[path.relative_to(case).as_posix()] = content
        if sum(map(len, data.values())) > self.PACKET_LIMIT:
            raise ValueError("public packet exceeds the packet size limit")
        self._data = MappingProxyType(data)


@runtime_checkable
class V2Port(Protocol):
    adapter_version: str

    def open_packet(self, packet_dir: Path) -> PacketFiles: ...

    def public_facts(self, files: PacketFiles) -> dict: ...

    def build_packet(self, record, raw_bytes: bytes) -> dict: ...

    def resolve_pointer(
        self, name: str, locator: str, contents: Mapping[str, str]
    ) -> tuple[bool, str]: ...


class SnapshotV2Port:
    """Phase-A adapter pinned to the 2026-10-17 acceptance snapshot."""

    adapter_version = V2_ADAPTER_VERSION

    def open_packet(self, packet_dir: Path) -> PacketFiles:
        return PacketFiles(Path(packet_dir))

    def public_facts(self, files: PacketFiles) -> dict:
        return public_facts(files)

    def build_packet(self, record, raw_bytes: bytes) -> dict:
        return build_packet(record, raw_bytes)

    def resolve_pointer(
        self, name: str, locator: str, contents: Mapping[str, str]
    ) -> tuple[bool, str]:
        # Mirrors the frozen grader locator semantics on public bytes only.
        return _resolve(name, locator, dict(contents))
