"""P7: SHA256 archive of the V3.2 raw experiment data."""
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S2P = ROOT / "data" / "eval" / "v32_ab"
ARCHIVE = ROOT / "data" / "archive" / "v32_ab"


def sha256(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()


def main() -> int:
    entries = []
    copies = [("ledger/V3_2_REAL_AB_LEDGER.jsonl",
               ROOT / "V3_2_REAL_AB_LEDGER.jsonl"),
              ("plan/V3_2_REAL_AB_PLAN.json", ROOT / "V3_2_REAL_AB_PLAN.json"),
              ("checkpoints/checkpoints.sqlite3",
               S2P / "checkpoints.sqlite3")]
    for f in sorted(S2P.glob("*.json")):
        copies.append((f"report/{f.name}", f))
    for run_dir in sorted((S2P / "runs").iterdir()):
        for f in sorted(run_dir.glob("*.json")):
            copies.append((f"runs/{run_dir.name}/{f.name}", f))
    for sc in sorted((S2P / "grading").glob("*/scorecard.json")):
        copies.append((f"grading/{sc.parent.name}/scorecard.json", sc))
    for rel, src in copies:
        if not src.is_file():
            continue
        dst = ARCHIVE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        entries.append({"path": rel, "sha256": sha256(dst),
                        "bytes": dst.stat().st_size})
    shards_3way = ROOT / "data" / "eval" / "v32_ab" / "shards_3way"
    if shards_3way.is_dir():
        for f in sorted(shards_3way.iterdir()):
            dst = ARCHIVE / f"void_shards_3way/{f.name}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            entries.append({"path": f"void_shards_3way/{f.name}",
                            "sha256": sha256(dst),
                            "bytes": dst.stat().st_size})
    manifest = {
        "manifest": "V3_2_ARCHIVE/v1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "archive_root": str(ARCHIVE), "file_count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries), "files": entries,
    }
    (ROOT / "V3_2_ARCHIVE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"archived {len(entries)} files "
          f"({manifest['total_bytes']} bytes) -> {ARCHIVE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
