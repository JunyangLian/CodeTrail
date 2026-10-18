"""P1.2 Data Flywheel: real run traces -> Failure Intelligence -> pending
review -> dedup -> E2 regression pool -> next strategy validation.

Governance rules (user directive):
- every case carries its data source, source_group_id, error mechanism,
  review identity, version info and source hash - nothing anonymous enters
  the pool;
- dedup on (source, error_mechanism, task_scope) at BOTH the pending queue
  and the regression pool;
- user-uploaded data NEVER auto-enters the pool: uploads are skipped unless
  the explicit feature flag ``allow_user_uploads`` is set by an operator;
- review is a human act with a recorded identity; auto-attribution only
  ever produces ``candidate`` entries.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from genomeqc_agent.evolution.failure_intel import _classify
from genomeqc_agent.evolution.regression_pool import RegressionPool

PENDING_FILE = "pending_cases.v1.jsonl"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def source_group_of(packet_id: str) -> str:
    """Derive the source group (accession family) from a packet id."""
    match = re.search(r"(NC_\d+_\d+|AC_\d+|\w+_\d+\.\d+)", packet_id or "")
    return match.group(1) if match else (packet_id or "unknown")


class DataFlywheel:
    def __init__(self, pool: RegressionPool, state_dir: Path,
                 allow_user_uploads: bool = False):
        self.pool = pool
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.pending_path = self.state_dir / PENDING_FILE
        self.allow_user_uploads = allow_user_uploads
        self._records: list[dict] = []
        if self.pending_path.is_file():
            for line in self.pending_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._records.append(json.loads(line))

    # -- ingestion ----------------------------------------------------------
    def ingest_run_states(self, states: list[dict], source: str) -> dict:
        """Extract failure / high-risk cases from real run states
        (QCGraph.get_state dumps or persisted run records)."""
        cases = []
        for state in states:
            runtime = state.get("runtime") or {}
            packet_id = (state.get("input") or {}).get("packet_id", "")
            if source == "user_upload" and not self.allow_user_uploads:
                continue
            if packet_id.startswith("upload_") and not self.allow_user_uploads:
                continue
            status = runtime.get("status")
            error = runtime.get("last_error") or ""
            evidence = state.get("evidence") or {}
            unresolved = evidence.get("unresolved") or []
            is_failure = status == "failed" and error
            is_high_risk = (status == "succeeded" and unresolved
                            and (state.get("report") or {}).get(
                                "evidence_validity") == "partial")
            if not (is_failure or is_high_risk):
                continue
            category, mechanism = _classify(error) if is_failure \
                else ("high_risk_success", "partial evidence with unresolved pointers")
            cases.append(self._pending_case(
                run_id=state.get("run_id", ""), packet_id=packet_id,
                source=source, category=category, mechanism=mechanism,
                evidence={"error": error[:300], "unresolved": unresolved,
                          "decision": (state.get("report") or {}).get("qc_decision")},
                input_sha256=(state.get("input") or {}).get("input_sha256", ""),
                prompt_version=(state.get("strategy") or {}).get("prompt_version", "")))
        return self._enqueue(cases)

    def ingest_failure_entries(self, entries: list[dict], source: str) -> dict:
        """Ingest entries already attributed by Failure Intelligence
        (failure_intel pool lines) - the historical-trace path."""
        cases = []
        for entry in entries:
            if source == "user_upload" and not self.allow_user_uploads:
                continue
            cases.append(self._pending_case(
                run_id=entry.get("run_id", ""),
                packet_id=entry.get("case_id", ""),
                source=source, category=entry.get("category", "unclassified"),
                mechanism=entry.get("mechanism") or "unattributed",
                evidence={"error_evidence": entry.get("error_evidence") or {}},
                input_sha256=entry.get("input_sha256", ""),
                prompt_version=entry.get("prompt_version", "")))
        return self._enqueue(cases)

    def _pending_case(self, run_id: str, packet_id: str, source: str,
                      category: str, mechanism: str, evidence: dict,
                      input_sha256: str, prompt_version: str) -> dict:
        return {
            "kind": "case",
            "case_id": f"FW-{run_id or packet_id}",  # uniqueness per run+packet
            "run_id": run_id,
            "packet_id": packet_id,
            "source": source,
            "source_group_id": source_group_of(packet_id),
            "category": category,
            "error_mechanism": mechanism,
            "evidence": evidence,
            "input_sha256": input_sha256,
            "prompt_version": prompt_version,
            "status": "pending_review",
            "reviewer": None,
            "at": _now(),
        }

    def _enqueue(self, cases: list[dict]) -> dict:
        added, skipped_dup = 0, 0
        for case in cases:
            if self._dup(case):
                skipped_dup += 1
                continue
            self._records.append(case)
            added += 1
        self._save()
        return {"ingested": added, "skipped_duplicate": skipped_dup,
                "pending_total": len(self.pending_cases())}

    def _dup(self, case: dict) -> bool:
        """Dedup on (source, mechanism, scope) across the queue AND the pool."""
        key = (case["source"], case["error_mechanism"], case["source_group_id"])
        for record in self._records:
            if record.get("kind") != "case":
                continue
            if (record["source"], record["error_mechanism"],
                    record["source_group_id"]) == key:
                return True
        for entry in self.pool.entries.values():
            if (entry["source"] == case["source"]
                    and entry["error_mechanism"] == case["error_mechanism"]
                    and entry.get("task_scope") == case["source_group_id"]):
                return True
        return False

    # -- review ---------------------------------------------------------------
    def review(self, case_id: str, reviewer: str, decision: str,
               note: str = "") -> dict:
        """Human review with recorded identity. approve -> regression pool
        (human_approved); reject -> receipt only, pool untouched.

        A SIMULATED reviewer identity can never promote a pool entry past
        ``candidate`` - same principle as the approval gate (P2): simulation
        must not masquerade as human review.
        """
        from genomeqc_agent.evolution.flags import is_simulated_identity
        if decision not in ("approve", "reject"):
            raise ValueError(f"illegal decision: {decision}")
        case = next((r for r in self._records
                     if r.get("kind") == "case" and r["case_id"] == case_id), None)
        if case is None:
            raise KeyError(f"unknown case: {case_id}")
        record = {"kind": "review", "case_id": case_id, "reviewer": reviewer,
                  "decision": decision, "note": note, "at": _now(),
                  "simulated": is_simulated_identity(reviewer)}
        self._records.append(record)
        case["status"] = f"review_{decision}d"
        case["reviewer"] = reviewer
        entry_id = ""
        if decision == "approve":
            entry_id = self.pool.add_v3_case(
                family=case["error_mechanism"][:60] or "flywheel.case",
                error_mechanism=case["error_mechanism"],
                task_scope=case["source_group_id"],
                evidence={"case_id": case_id, "run_id": case["run_id"],
                          "packet_id": case["packet_id"],
                          "input_sha256": case["input_sha256"],
                          "prompt_version": case["prompt_version"],
                          "evidence": case["evidence"],
                          "reviewed_by": reviewer},
                status="candidate")
            if entry_id and not record["simulated"]:
                self.pool.set_status(entry_id, "human_approved", reviewer)
        self._save()
        self.pool.save()
        return {**record, "pool_entry_id": entry_id}

    # -- views ------------------------------------------------------------------
    def pending_cases(self) -> list[dict]:
        reviewed = {r["case_id"] for r in self._records if r.get("kind") == "review"}
        return [r for r in self._records
                if r.get("kind") == "case" and r["case_id"] not in reviewed]

    def all_records(self) -> list[dict]:
        return list(self._records)

    def _save(self) -> None:
        self.pending_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True)
                      for r in self._records) + "\n", encoding="utf-8")
