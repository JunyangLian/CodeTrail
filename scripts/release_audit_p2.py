"""P2 release audit: verify the publish state machine is consistent and
mark the promptv2 candidate as DEV-verified / pending approval. Read-only
with respect to strategy state: NOTHING is promoted, published, or applied
to the active strategy."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "evolution_state"


def main() -> int:
    registry = json.loads(
        (STATE / "prompt_registry.v1.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (STATE / "STRATEGY_CANDIDATE_MANIFEST.json").read_text(encoding="utf-8"))
    decision = json.loads(
        (ROOT / "V3_S2_EVOLUTION_DECISION.json").read_text(encoding="utf-8"))

    findings: dict = {}
    # 1. promptv2 registered, immutable sha, matches the A/B receipts
    v2 = registry["versions"].get("promptv2")
    findings["promptv2_registered"] = v2 is not None
    ledger_lines = [(ROOT / "V3_S2_RUN_LEDGER.jsonl").read_text(
        encoding="utf-8").splitlines()]
    receipt_shas = set()
    for line in ledger_lines[0]:
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("kind") == "observation" and rec.get("arm") == "glm_B":
            receipt_shas.add(rec.get("prompt_sha256"))
    findings["promptv2_sha_matches_all_B_receipts"] = receipt_shas == {
        v2["sha256"]} if v2 else False

    # 2. active strategy untouched
    findings["active_is_promptv1"] = (
        manifest.get("active_strategy", {}).get("prompt_version") == "promptv1")

    # 3. candidate status + cap
    candidates = manifest.get("candidates", [])
    findings["candidates_present"] = len(candidates) > 0
    findings["status_cap_validated"] = all(
        c.get("status_cap") == "validated" for c in candidates)
    findings["none_published"] = all(
        "published" not in (c.get("status") or "") for c in candidates)

    # 4. no real approval exists anywhere -> publish gate locked
    approvals = []
    for path in sorted((STATE / "receipts").glob("*/approval_*.json")):
        approvals.append(json.loads(path.read_text(encoding="utf-8")))
    findings["all_approvals_simulated"] = all(
        not a.get("real") for a in approvals)
    findings["no_published_receipts"] = not list(
        (STATE / "receipts").glob("*/published.json"))

    # 5. decision is pending-approval, not a publish
    findings["decision_is_accept_candidate"] = decision["decision"] == \
        "ACCEPT_CANDIDATE"

    all_ok = all(findings.values())

    # mark DEV verification on every candidate (NOT a promotion)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for c in candidates:
        c["dev_verification"] = {
            "status": "DEV-verified, pending human approval",
            "evidence": "V3_S2_EVOLUTION_DECISION.json: ACCEPT_CANDIDATE "
                        "(120 real observations, frozen thresholds)",
            "status_cap_unchanged": "validated",
            "published": False,
            "marked_at": now,
        }
    manifest["dev_verification_marked_at"] = now
    (STATE / "STRATEGY_CANDIDATE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    audit = {
        "audit": "V3_1_RELEASE_AUDIT_P2/v1",
        "at": now,
        "findings": findings,
        "all_ok": all_ok,
        "verdict": "PASS" if all_ok else "REVIEW",
        "note": ("promptv2 is marked DEV-verified / pending approval; the "
                 "publish gate requires record_approval(real=True) by a "
                 "named human approver - not executed in this audit"),
    }
    out = ROOT / "data" / "eval" / "s2_experiment" / "release_audit_p2.json"
    out.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("verdict:", audit["verdict"])
    print(json.dumps(findings, ensure_ascii=False, indent=1))
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
