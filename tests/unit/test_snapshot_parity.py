"""Snapshot integrity: hashes vs manifest; live parity with V2 when present."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "src" / "genomeqc_agent" / "adapters" / "v2_snapshot"
V2_REPO = Path(os.environ.get("GENOMEQC_V2_REPO",
                              r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"))

SOURCE_OF = {
    "visible_input.py": "bench/visible_input.py",
    "facts.py": "bench/organelle_v2/recovery/facts.py",
    "verify.py": "bench/organelle_v2/recovery/verify.py",
    "v2_common.py": "bench/organelle_v2/common.py",
    "genbank.py": "bench/organelle_v2/normalize/genbank.py",
    "seqio.py": "bench/organelle_v2/normalize/seqio.py",
    "feature_contract.py": "bench/organelle_v2/normalize/feature_contract.py",
    "translation.py": "bench/organelle_v2/normalize/translation.py",
    "readonly.py": "bench/inspect_adapter/readonly.py",
    "genbank_to_packet.py": "bench/organelle_v2/normalize/genbank_to_packet.py",
}
PATCHED = {"readonly.py", "genbank_to_packet.py"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_covers_all_snapshot_files():
    manifest = json.loads((SNAPSHOT_DIR / "SNAPSHOT_MANIFEST.json").read_text(encoding="utf-8"))
    recorded = {entry["snapshot"] for entry in manifest["files"]}
    on_disk = {p.name for p in SNAPSHOT_DIR.glob("*.py")} - {"__init__.py"}
    assert recorded == on_disk == set(SOURCE_OF)


def test_snapshot_hashes_match_manifest():
    manifest = json.loads((SNAPSHOT_DIR / "SNAPSHOT_MANIFEST.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        assert _sha256(SNAPSHOT_DIR / entry["snapshot"]) == entry["snapshot_sha256"], entry["snapshot"]
        if entry["patched"]:
            assert not entry["byte_identical"]
        else:
            assert entry["byte_identical"]


def test_patched_files_only_differ_in_import_lines():
    """Honesty check: patched snapshots keep the V2 functional body intact.

    Comments are excluded from the drift comparison (they document the patch
    but cannot change behaviour); every executable line must exist verbatim
    in the V2 original.
    """

    def _code_lines(text: str) -> list[str]:
        return [line for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]

    for name in PATCHED:
        snapshot_lines = _code_lines((SNAPSHOT_DIR / name).read_text(encoding="utf-8"))
        v2_lines = _code_lines((V2_REPO / SOURCE_OF[name]).read_text(encoding="utf-8"))
        v2_set = set(v2_lines)
        redirects = 0
        for line in snapshot_lines:
            if "genomeqc_agent.adapters.v2_snapshot" in line:
                redirects += 1  # the documented import redirect itself
                continue
            assert line in v2_set, f"unexpected drift in {name}: {line!r}"
        assert redirects >= 1, f"{name} lost its import redirect"


@pytest.mark.skipif(not V2_REPO.is_dir(), reason="V2 repo not available on this machine")
def test_live_parity_with_v2_when_present():
    """Byte parity vs the live V2 tree for unpatched files (read-only check)."""
    for name, source in SOURCE_OF.items():
        if name in PATCHED:
            continue
        assert _sha256(SNAPSHOT_DIR / name) == _sha256(V2_REPO / source), name
