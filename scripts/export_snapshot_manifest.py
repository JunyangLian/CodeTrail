"""Regenerate SNAPSHOT_MANIFEST.json against the live V2 tree.

Run only at G0/G6 re-audit time; tests enforce integrity afterwards.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

V2_REPO = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2")
SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "src" / "genomeqc_agent" / "adapters" / "v2_snapshot"

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
PATCH_NOTES = {
    "readonly.py": "import line bench.v2.runtime -> genomeqc_agent.adapters.v2_snapshot.visible_input (docstring comment added)",
    "genbank_to_packet.py": "3 import lines redirected to v2_snapshot modules (v2_common, feature_contract+seqio, genbank x2)",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def v2_head() -> str:
    return subprocess.run(["git", "-C", str(V2_REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    files = []
    for name, source in SOURCE_OF.items():
        snap = SNAPSHOT_DIR / name
        orig = V2_REPO / source
        patched = name in PATCH_NOTES
        files.append({
            "snapshot": name, "source": source,
            "original_sha256": sha256(orig), "snapshot_sha256": sha256(snap),
            "byte_identical": sha256(orig) == sha256(snap),
            "patched": patched, "patch_note": PATCH_NOTES.get(name),
        })
    manifest = {
        "manifest_version": 1,
        "generated": date.today().isoformat(),
        "source_repo": str(V2_REPO),
        "source_commit": v2_head(),
        "source_git_dirty": subprocess.run(
            ["git", "-C", str(V2_REPO), "status", "--short"],
            capture_output=True, text=True, check=True).stdout.strip() != "",
        "acceptance_doc": "bench/organelle_v2/docs/V2_FINAL_ACCEPTANCE.md",
        "acceptance_doc_sha256": sha256(V2_REPO / "bench/organelle_v2/docs/V2_FINAL_ACCEPTANCE.md"),
        "export_scope": "pure public-byte functions only; no private expected/rubric/oracle, "
                        "no .env, no model logs",
        "files": files,
    }
    out = SNAPSHOT_DIR / "SNAPSHOT_MANIFEST.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    print(f"manifest written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
