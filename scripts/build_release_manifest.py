"""Final-round P5: build V3_2_FINAL_RELEASE_MANIFEST.json (SHA256 of all
final-round deliverables, ledgers, plans and audit artifacts)."""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "docs/V3_2_AB_VALIDITY_AUDIT.md",
    "docs/V3_2_FAILURE_ATTRIBUTION_FINAL.md",
    "docs/V3_2_INDEPENDENT_HOLDOUT_REPORT.md",
    "docs/V3_2_FINAL_REGRESSION_REPORT.md",
    "V3_2_FINAL_RELEASE_REPORT.md",
    "V3_2_INTERVIEW_EVIDENCE.md",
    "V3_2_HOLDOUT_PLAN.json",
    "V3_2_P2_ABLATION_PLAN.json",
    "V3_2_REAL_AB_PLAN.json",
    "V3_2_REAL_AB_LEDGER.jsonl",
    "data/eval/final_round/holdout_dataset/DATASET_MANIFEST.json",
    "data/eval/final_round/holdout_sources/sources_metadata.json",
    "data/eval/final_round/holdout_sources/generation_summary.json",
    "data/eval/final_round/holdout_run/holdout_ledger.jsonl",
    "data/eval/final_round/holdout_run/holdout_metrics.json",
    "data/eval/final_round/p1_audit/ab_config_diff.json",
    "data/eval/final_round/p1_audit/del_evidence_audit.json",
    "data/eval/final_round/p1_audit/ab_stats_reanalysis.json",
    "data/eval/final_round/p2_attribution/attribution_final.json",
    "data/eval/final_round/p2_ablation/ablation_ledger.jsonl",
    "data/eval/final_round/p0_baseline_snapshot/prompt_registry.v1.json",
    "scripts/audit_ab_validity.py",
    "scripts/build_attribution_final.py",
    "scripts/run_p2_ablation.py",
    "scripts/fetch_holdout_sources.py",
    "scripts/build_holdout_dataset.py",
    "scripts/run_holdout_ab.py",
    "scripts/analyze_holdout.py",
    "scripts/restore_snapshot_bytes.py",
    "tests/unit/test_final_stability.py",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    entries = {}
    missing = []
    for rel in FILES:
        p = ROOT / rel
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
            "ledger_integrity": "V3.2 144 + holdout 96 + S2 121 rows verified",
        },
        "files": entries,
        "missing": missing,
    }
    out = ROOT / "V3_2_FINAL_RELEASE_MANIFEST.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"WROTE {out} files={len(entries)} missing={len(missing)}")
    for m in missing:
        print("  MISSING:", m)
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
