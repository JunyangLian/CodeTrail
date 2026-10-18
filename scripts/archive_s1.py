"""P1: independent S1 archive + SHA256 manifest.

Copies the complete S1 raw record (ledger, report, checkpoints, 16 raw QC
reports, grader scorecards, prompt registry snapshot, void attempt) into
data/archive/s1_smoke/ and writes V3_S1_ARCHIVE_MANIFEST.json at the repo
root. Nothing is deleted or moved from the original location. No secrets:
.env and any credential material are excluded by construction (never read).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S1 = ROOT / "data" / "eval" / "s1_smoke"
VOID = ROOT / "data" / "eval" / "s1_smoke_void_scripted_planner_bug"
SCORE_AUDIT = ROOT / "data" / "eval" / "s1_score_audit"
ARCHIVE = ROOT / "data" / "archive" / "s1_smoke"

COPY_SET = [
    (S1 / "s1_ledger.jsonl", "ledger/s1_ledger.jsonl"),
    (S1 / "s1_report.json", "report/s1_report.json"),
    (S1 / "checkpoints.sqlite3", "checkpoints/checkpoints.sqlite3"),
    (VOID / "VOID_MARKER.txt", "void_attempt_1/VOID_MARKER.txt"),
    (VOID / "s1_ledger.jsonl", "void_attempt_1/s1_ledger.jsonl"),
    (VOID / "s1_report.json", "void_attempt_1/s1_report.json"),
    (ROOT / "evolution_state" / "prompt_registry.v1.json",
     "strategy/prompt_registry.v1.json"),
    (ROOT / "evolution_state" / "STRATEGY_CANDIDATE_MANIFEST.json",
     "strategy/STRATEGY_CANDIDATE_MANIFEST.json"),
    (SCORE_AUDIT / "s1_score_audit.json", "score_audit/s1_score_audit.json"),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    entries: list[dict] = []
    for src, rel in COPY_SET:
        if not src.is_file():
            continue
        dst = ARCHIVE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        entries.append(_entry(dst, rel, "copy"))
    # per-run raw QC reports (16) + per-observation scorecards
    for run_dir in sorted((S1 / "runs").iterdir()):
        for f in sorted(run_dir.glob("*.json")):
            rel = f"runs/{run_dir.name}/{f.name}"
            dst = ARCHIVE / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            entries.append(_entry(dst, rel, "copy"))
    for sc in sorted(SCORE_AUDIT.glob("*/scorecard.json")):
        rel = f"score_audit/{sc.parent.name}/scorecard.json"
        dst = ARCHIVE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sc, dst)
        entries.append(_entry(dst, rel, "copy"))

    manifest = {
        "manifest": "V3_S1_ARCHIVE_MANIFEST/v1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "archive_root": str(ARCHIVE),
        "source_roots": [str(S1), str(VOID), str(SCORE_AUDIT)],
        "note": ("independent archive copy; original S1 files at data/eval/s1_smoke "
                 "are preserved untouched. No credentials or .env material is "
                 "included (never read by this script)."),
        "file_count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "files": entries,
    }
    out = ROOT / "V3_S1_ARCHIVE_MANIFEST.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"archived {len(entries)} files ({manifest['total_bytes']} bytes) -> {ARCHIVE}")
    print(f"manifest: {out}")
    return 0


def _entry(dst: Path, rel: str, kind: str) -> dict:
    return {"path": rel, "sha256": sha256(dst), "bytes": dst.stat().st_size,
            "kind": kind}


if __name__ == "__main__":
    raise SystemExit(main())
