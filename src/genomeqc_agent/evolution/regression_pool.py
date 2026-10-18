"""M4 E2 Regression Memory: versioned pool of confirmed failure experiences.

Sources (all read-only):
- V2 E2_REGRESSION_POOL.v2.json (10 human-reviewed entries; E2-2026-006 is
  explicitly deferred to V3)
- V3-confirmed cases: hallucinated pointers (intercepted), planner infra
  failures, null tool_name contract normalisation

Dedup key: (source, error_mechanism, task_scope). Statuses:
candidate -> human_approved -> formal_regression. Only formal_regression
entries are executed by the deterministic regression runner.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

STATUSES = ("candidate", "human_approved", "formal_regression")
SIMULATION_MARK = "offline_simulation"


class RegressionPool:
    def __init__(self, pool_path: Path):
        self.pool_path = Path(pool_path)
        self.entries: dict[str, dict] = {}
        if self.pool_path.is_file():
            for line in self.pool_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    self.entries[item["entry_id"]] = item

    # -- ingestion ----------------------------------------------------------
    def import_v2_pool(self, v2_pool_path: Path) -> int:
        """Read-only import of the human-reviewed V2 pool."""
        v2 = json.loads(Path(v2_pool_path).read_text(encoding="utf-8"))
        added = 0
        for entry in v2["entries"]:
            item = {
                "entry_id": f"V3-{entry['id']}",
                "origin": "v2_e2_pool",
                "source": "v2_e2_regression_pool_v2",
                "family": entry["family"],
                "error_mechanism": entry["family"],
                "task_scope": "organelle_v2 output contract",
                "status": ("formal_regression"
                           if entry["status"] == "converted_to_regression"
                           else "candidate"),
                "regression_test": entry.get("regression_test"),
                "note": entry.get("transition_note", ""),
                "review_status": "human_confirmed (V2 pool governance)",
            }
            if self._add(item):
                added += 1
        return added

    def add_v3_case(self, family: str, error_mechanism: str, task_scope: str,
                    evidence: dict, regression_test: str | None = None,
                    status: str = "candidate") -> str:
        digest = json.dumps([family, error_mechanism, task_scope], sort_keys=True)
        entry_id = "V3-" + __import__("hashlib").sha256(digest.encode()).hexdigest()[:8]
        added = self._add({
            "entry_id": entry_id, "origin": "v3_trace", "source": "v3_g4_traces",
            "family": family, "error_mechanism": error_mechanism,
            "task_scope": task_scope, "status": status,
            "regression_test": regression_test, "evidence": evidence,
            "review_status": "auto_attributed",
        })
        return entry_id if added else ""

    def _add(self, item: dict) -> bool:
        """Dedup on (source, error_mechanism, task_scope)."""
        for existing in self.entries.values():
            if (existing["source"] == item["source"]
                    and existing["error_mechanism"] == item["error_mechanism"]
                    and existing["task_scope"] == item["task_scope"]):
                return False
        item["first_seen"] = datetime.now(UTC).isoformat(timespec="seconds")
        item["simulation"] = SIMULATION_MARK
        self.entries[item["entry_id"]] = item
        return True

    def set_status(self, entry_id: str, status: str, reviewer: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"illegal status: {status}")
        if entry_id not in self.entries:
            raise KeyError(entry_id)
        self.entries[entry_id]["status"] = status
        self.entries[entry_id]["review_status"] = f"human_approved by {reviewer}"

    # -- persistence ----------------------------------------------------------
    def save(self) -> None:
        self.pool_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(self.entries[k], ensure_ascii=False, sort_keys=True)
                 for k in sorted(self.entries)]
        self.pool_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # -- deterministic regression execution -----------------------------------
    def run_formal_regressions(self, tests_root: Path) -> dict:
        """Execute the local regression test mapped to each formal entry.
        V2-origin entries pointing into the frozen V2 tree are NOT executed
        here (V2 stays frozen); their evidence remains the V2 run receipts."""
        results = []
        for entry in self.entries.values():
            if entry["status"] != "formal_regression" or not entry.get("regression_test"):
                continue
            test_id = entry["regression_test"]
            if test_id.startswith("bench/"):
                results.append({"entry_id": entry["entry_id"], "family": entry["family"],
                                "test": test_id, "executed": False,
                                "note": "V2-frozen test; evidence = V2 run receipts"})
                continue
            node = test_id.split("::", 1)
            target = node[0] if len(node) == 1 else f"{node[0]}::{node[1]}"
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", target],
                capture_output=True, text=True, timeout=300, check=False,
                cwd=str(tests_root))
            results.append({"entry_id": entry["entry_id"], "family": entry["family"],
                            "test": target, "executed": True,
                            "passed": proc.returncode == 0,
                            "output_tail": proc.stdout[-200:]})
        return {
            "simulation": SIMULATION_MARK,
            "executed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "results": results,
            "all_passed": all(r.get("passed", True) for r in results),
        }
