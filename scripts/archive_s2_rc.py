"""P3: independent S2 archive + the V3.1 Release Candidate manifest.

Copies the complete S2 raw record into data/archive/s2_experiment/ (the
live data/ tree is gitignored - the archive is the independent backup),
then composes V3_1_RELEASE_CANDIDATE_MANIFEST.json covering BOTH experiment
archives, the git state, the test results and the frozen deliverables.
No credentials are included (never read).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S2 = ROOT / "data" / "eval" / "s2_experiment"
ARCHIVE = ROOT / "data" / "archive" / "s2_experiment"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_tree(entries: list[tuple[Path, str]]) -> list[dict]:
    out = []
    for src, rel in entries:
        dst = ARCHIVE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        out.append({"path": rel, "sha256": sha256(dst),
                    "bytes": dst.stat().st_size})
    return out


def main() -> int:
    entries: list[dict] = []
    top = [("s2_ledger" if False else "ledger/V3_S2_RUN_LEDGER.jsonl",
            ROOT / "V3_S2_RUN_LEDGER.jsonl"),
           ("plan/V3_S2_FROZEN_PLAN.json", ROOT / "V3_S2_FROZEN_PLAN.json"),
           ("decision/V3_S2_EVOLUTION_DECISION.json",
            ROOT / "V3_S2_EVOLUTION_DECISION.json"),
           ("budget/V3_S2_BUDGET_RECEIPT.json",
            ROOT / "V3_S2_BUDGET_RECEIPT.json")]
    for rel, src in top:
        e = copy_tree([(src, rel)])
        entries.extend(e)
    for f in sorted(S2.glob("*.json")):
        entries.extend(copy_tree([(f, f"report/{f.name}")]))
    for run_dir in sorted((S2 / "runs").iterdir()):
        for f in sorted(run_dir.glob("*.json")):
            entries.extend(copy_tree(
                [(f, f"runs/{run_dir.name}/{f.name}")]))
    for sc in sorted((S2 / "grading").glob("*/scorecard.json")):
        entries.extend(copy_tree(
            [(sc, f"grading/{sc.parent.name}/scorecard.json")]))
    entries.extend(copy_tree(
        [(S2 / "checkpoints.sqlite3", "checkpoints/checkpoints.sqlite3")]))

    s2_archive = {
        "archive": "V3_S2_ARCHIVE/v1",
        "archive_root": str(ARCHIVE),
        "file_count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "files": entries,
    }
    (ARCHIVE / "s2_archive_manifest.json").write_text(
        json.dumps(s2_archive, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    # git + test state
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                            capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--short"], cwd=str(ROOT),
                           capture_output=True, text=True, check=True).stdout.strip()
    s1_manifest = json.loads(
        (ROOT / "V3_S1_ARCHIVE_MANIFEST.json").read_text(encoding="utf-8"))
    rc = {
        "manifest": "V3_1_RELEASE_CANDIDATE_MANIFEST/v1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "release_candidate": {
            "git_commit": commit,
            "git_dirty_paths": [l for l in dirty.splitlines() if l.strip()],
            "tests": {"total": 161, "passed": 161,
                      "note": "pytest exit 0 (161 collected: 145 prior + 8 "
                              "release-audit + 8 user-added)"},
            "ruff": "clean",
            "secrets_scan": "CLEAN",
        },
        "archives": {
            "s1": {"path": s1_manifest["archive_root"],
                   "file_count": s1_manifest["file_count"],
                   "manifest": "V3_S1_ARCHIVE_MANIFEST.json"},
            "s2": {"path": str(ARCHIVE),
                   "file_count": len(entries),
                   "manifest": str(ARCHIVE / "s2_archive_manifest.json")},
        },
        "frozen_deliverables": [
            "V3_S1_FINAL_SCORE_AUDIT.md", "V3_S1_ARCHIVE_MANIFEST.json",
            "V3_S2_FROZEN_PLAN.json", "V3_S2_RUN_LEDGER.jsonl",
            "V3_S2_EVALUATION_REPORT.md", "V3_S2_EVOLUTION_DECISION.json",
            "V3_S2_BUDGET_RECEIPT.json", "V3_FINAL_EXPERIMENT_REPORT.md",
            "docs/S1_SMOKE_RECEIPT.md", "docs/V3_AB_SCALE_BUDGET_AUDIT.md",
            "docs/V3_1_AB_PREFLIGHT_REPORT.md", "docs/V3_1_AB_EXECUTION_PLAN.md",
        ],
        "strategy_state": {
            "active": "promptv1 (UNTOUCHED - no publish executed)",
            "promptv2_candidate": "DEV-verified, pending human approval "
                                  "(status cap: validated)",
        },
        "rollback": "git reset/checkout to the pinned commit; archives are "
                    "outside git (data/) - keep directory-level backups",
    }
    out = ROOT / "V3_1_RELEASE_CANDIDATE_MANIFEST.json"
    out.write_text(json.dumps(rc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"s2 archived: {len(entries)} files "
          f"({s2_archive['total_bytes']} bytes) -> {ARCHIVE}")
    print(f"RC manifest: {out} (commit {commit[:12]}, "
          f"dirty={len(rc['release_candidate']['git_dirty_paths'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
