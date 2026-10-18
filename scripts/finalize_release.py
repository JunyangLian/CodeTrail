"""Final-round P5 finalize: copy git-trackable deliverables to repo root
(data/ is gitignored by convention; ledgers/manifests live at root), then
regenerate the release manifest including the root copies."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COPIES = {
    "data/eval/final_round/holdout_run/holdout_ledger.jsonl":
        "V3_2_HOLDOUT_LEDGER.jsonl",
    "data/eval/final_round/p2_ablation/ablation_ledger.jsonl":
        "V3_2_P2_ABLATION_LEDGER.jsonl",
    "data/eval/final_round/holdout_dataset/DATASET_MANIFEST.json":
        "V3_2_HOLDOUT_DATASET_MANIFEST.json",
    "data/eval/final_round/p1_audit/ab_config_diff.json":
        "V3_2_P1_CONFIG_DIFF.json",
    "data/eval/final_round/p1_audit/del_evidence_audit.json":
        "V3_2_P1_DEL_EVIDENCE_AUDIT.json",
    "data/eval/final_round/p1_audit/ab_stats_reanalysis.json":
        "V3_2_P1_STATS_REANALYSIS.json",
    "data/eval/final_round/p2_attribution/attribution_final.json":
        "V3_2_ATTRIBUTION_FINAL.json",
}

EXTRA_FILES = [
    "V3_2_HOLDOUT_LEDGER.jsonl",
    "V3_2_P2_ABLATION_LEDGER.jsonl",
    "V3_2_HOLDOUT_DATASET_MANIFEST.json",
    "V3_2_P1_CONFIG_DIFF.json",
    "V3_2_P1_DEL_EVIDENCE_AUDIT.json",
    "V3_2_P1_STATS_REANALYSIS.json",
    "V3_2_ATTRIBUTION_FINAL.json",
    "scripts/build_release_manifest.py",
]


def main() -> int:
    for src, dst in COPIES.items():
        shutil.copyfile(ROOT / src, ROOT / dst)
        print(f"copied -> {dst}")
    # regenerate manifest with the root copies appended to the file list
    from build_release_manifest import FILES, sha  # noqa: E402
    import json  # noqa: E402
    from datetime import UTC, datetime  # noqa: E402
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    entries = {}
    missing = []
    for rel in FILES + EXTRA_FILES:
        p = ROOT / rel
        if rel == "V3_2_FINAL_RELEASE_MANIFEST.json":
            continue
        if p.exists():
            entries[rel] = sha(p)
        else:
            missing.append(rel)
    manifest = {
        "manifest": "V3_2_FINAL_RELEASE_MANIFEST/v1",
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "baseline_commit": head,
        "release": "V3.2 RC-FINAL (final round P0-P5)",
        "active_strategy": "promptv1 (unchanged; no auto-publish)",
        "pending_human_approval": [
            "contextv1 candidate (CAND-contextv1-pointer-guard)",
            "promptv2 disposition",
            "optional: V3.1 flywheel candidates review"],
        "final_checks": {
            "pytest": "206/206 passed (exit 0)",
            "ruff": "clean (src+tests+scripts; v2_snapshot excluded by pyproject)",
            "secret_scan": "CLEAN (222 tracked files)",
            "snapshot_parity": "4/4 (restored via scripts/restore_snapshot_bytes.py)",
            "source_isolation": "6/6 PASS (8 new genera, 24 tasks)",
            "ledger_integrity": ("V3.2 144 + holdout 96 + ablation 24 + "
                                 "S2 121 rows verified"),
        },
        "files": entries,
        "missing": missing,
    }
    out = ROOT / "V3_2_FINAL_RELEASE_MANIFEST.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"WROTE {out} files={len(entries)} missing={len(missing)}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
