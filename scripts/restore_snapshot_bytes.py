"""Restore v2_snapshot working-tree bytes to the manifest/V2 authority.

The committed blobs are LF/mixed (git clean-filter output); the manifest and
the live V2 tree use CRLF. autocrlf smudge cannot reconstruct CRLF for mixed
files, so we restore deterministically:
  - unpatched files: byte-copy from the live V2 repo (the parity test's own
    authority), verified against the manifest hash;
  - patched files (import-redirect only): committed content normalized to
    CRLF, verified against the manifest hash.
Any verification failure aborts without writing further files.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

REPO = Path(r"d:\1_yanjiusheng\AssemblyGenomics-Copilot_V3")
V2 = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2")
SNAP = REPO / "src" / "genomeqc_agent" / "adapters" / "v2_snapshot"

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


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


manifest = json.loads((SNAP / "SNAPSHOT_MANIFEST.json").read_text(encoding="utf-8"))
expected = {e["snapshot"]: e["snapshot_sha256"] for e in manifest["files"]}

results = []
for name in SOURCE_OF:
    if name in PATCHED:
        blob = subprocess.run(
            ["git", "-C", str(REPO), "cat-file", "blob",
             f"HEAD:src/genomeqc_agent/adapters/v2_snapshot/{name}"],
            capture_output=True, check=True).stdout
        # patched files were CREATED at export time: readonly.py as LF,
        # genbank_to_packet.py rewritten from the V2 CRLF original -> try
        # the blob verbatim first, then the CRLF normalization
        candidates = [blob,
                      blob.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")]
        candidate = next((c for c in candidates
                          if sha(c) == expected[name]), None)
        if candidate is None:
            print(f"FAIL {name}: blob and CRLF-normalized forms both "
                  f"mismatch manifest {expected[name][:12]}")
            results.append((name, False, "", expected[name]))
            continue
        ok = True
    else:
        candidate = (V2 / SOURCE_OF[name]).read_bytes()
        ok = sha(candidate) == expected[name]
    results.append((name, ok, sha(candidate), expected[name]))
    if not ok:
        print(f"FAIL {name}: restored={sha(candidate)[:12]} "
              f"manifest={expected[name][:12]} - NOT written")
        continue
    (SNAP / name).write_bytes(candidate)
    print(f"OK   {name}")

failed = [r for r in results if not r[1]]
print(f"\nrestored {len(results) - len(failed)}/{len(results)}")
if failed:
    raise SystemExit(1)
