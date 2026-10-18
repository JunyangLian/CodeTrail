"""M1 Failure Intelligence: attribute real failures from historical traces.

Reads the G4 GLM/DeepSeek aggregates + checkpoint terminal states and emits a
versioned, append-only failure case pool (JSONL). Every entry carries
run_id/case_id/source/error evidence/model/config/review_status.

Category taxonomy (closed set, master-plan-aligned):
- model_planning_error      (e.g. hallucinated pointers, bad plans)
- tool_error                (tool-layer failures)
- infrastructure_error      (truncation/timeout/5xx/auth/config)
- data_or_scoring_defect    (task or scorer defect, V2 STAT_DEFINITIONS precedent)
- unclassified              (needs human review - never silently dropped)

Attribution is deterministic; review_status starts at ``auto_attributed``
and only a human review may move it to ``human_confirmed``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from genomeqc_agent.runtime.graph import QCGraph

CATEGORIES = ("model_planning_error", "tool_error", "infrastructure_error",
              "data_or_scoring_defect", "unclassified")
POOL_VERSION = "failure_cases.v1"
SIMULATION_MARK = "offline_simulation"


def _classify(error_text: str) -> tuple[str, str]:
    """Deterministic attribution from recorded error evidence."""
    text = error_text or ""
    if "output truncated" in text or "finish_reason=length" in text:
        return "infrastructure_error", "max_tokens too small for reasoning+content"
    if "ReadTimeout" in text or "transport failure" in text:
        return "infrastructure_error", "gateway read timeout / transport"
    if "503" in text or "too busy" in text:
        return "infrastructure_error", "provider capacity (503)"
    if "401" in text or "Token is invalid" in text:
        return "infrastructure_error", "invalid credentials"
    if "planner failed" in text or "plan contract violation" in text:
        return "model_planning_error", "planner output violated contract"
    return "unclassified", "no deterministic rule matched"


def _entry_id(case_id: str, run_id: str, kind: str) -> str:
    digest = hashlib.sha256(f"{case_id}|{run_id}|{kind}".encode()).hexdigest()[:10]
    return f"FC-{digest}"


def _failure_from_record(record: dict, source: str, model: dict) -> dict | None:
    if record.get("phase_a") != "no_report":
        return None
    error = record.get("error") or ""
    category, mechanism = _classify(error)
    return {
        "case_id": record.get("case"),
        "run_id": None,  # aggregate rows predate run-id capture in v1 receipts
        "source": source,
        "kind": "failure",
        "category": category,
        "mechanism": mechanism,
        "error_evidence": {"reason": error[:400],
                           "record_status": record.get("status")},
        "model": dict(model),
        "config": {"max_tokens": 4096 if "truncated" in error else "unknown"},
        "review_status": "auto_attributed",
    }


def _high_risk_successes_from_states(states: list[dict], source: str,
                                     model: dict) -> list[dict]:
    """Succeeded runs whose evidence chain carries defects (e.g. hallucinated
    pointers intercepted as unresolved) - the dangerous 'pass' neighbourhood."""
    entries = []
    for values in states:
        if not values.get("run_id") or not values.get("report"):
            continue
        evidence = values.get("evidence") or {}
        unresolved = evidence.get("unresolved") or []
        validity = (values.get("result") or {}).get("evidence_validity")
        if unresolved or validity == "partial":
            entries.append({
                "case_id": values["input"]["packet_id"],
                "run_id": values["run_id"],
                "source": source,
                "kind": "high_risk_success",
                "category": "model_planning_error",
                "mechanism": "planner cited pointers that fail mechanical resolution",
                "error_evidence": {"unresolved_pointers": unresolved,
                                   "evidence_validity": validity},
                "model": dict(model),
                "config": {},
                "review_status": "auto_attributed",
            })
    return entries


def build_failure_pool(eval_root: Path, model: dict,
                       states_loader=None) -> tuple[list[dict], dict]:
    """Collect failure + high-risk-success entries from one eval directory."""
    source = eval_root.name
    aggregate = json.loads((eval_root / "aggregate.json").read_text(encoding="utf-8"))
    entries: list[dict] = []
    for record in aggregate.get("records", []):
        failure = _failure_from_record(record, f"{source}/aggregate", model)
        if failure:
            entries.append(failure)
    if states_loader is not None:
        entries += _high_risk_successes_from_states(states_loader(eval_root),
                                                    f"{source}/checkpoint", model)
    report = {
        "pool_version": POOL_VERSION,
        "simulation": SIMULATION_MARK,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "candidates_root": aggregate.get("candidates_root"),
        "model": dict(model),
        "counts": {
            "cases_total": aggregate["denominators"]["cases_total"],
            "failures": sum(1 for e in entries if e["kind"] == "failure"),
            "high_risk_successes": sum(1 for e in entries
                                       if e["kind"] == "high_risk_success"),
            "by_category": {c: sum(1 for e in entries if e["category"] == c)
                            for c in CATEGORIES},
        },
    }
    return entries, report


def write_pool(entries: list[dict], reports: list[dict], out_dir: Path) -> dict:
    """Append-only pool write (JSONL); re-running with identical inputs
    produces identical entry_ids and must not duplicate entries."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pool_path = out_dir / "failure_cases.v1.jsonl"
    existing = {}
    if pool_path.is_file():
        for line in pool_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                existing[item["entry_id"]] = item
    for entry in entries:
        entry["entry_id"] = _entry_id(entry["case_id"], entry.get("run_id") or "",
                                      entry["kind"])
        entry.setdefault("first_seen", datetime.now(UTC).isoformat(timespec="seconds"))
        existing.setdefault(entry["entry_id"], entry)  # append-only: never overwrite
    lines = [json.dumps(existing[k], ensure_ascii=False, sort_keys=True)
             for k in sorted(existing)]
    pool_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report_path = out_dir / "attribution_report.v1.json"
    report = {
        "pool_version": POOL_VERSION,
        "simulation": SIMULATION_MARK,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "pool_file": str(pool_path),
        "entries_total": len(existing),
        "reports": reports,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return report


def collect_all(eval_roots: list[Path], model_by_root: dict[str, dict],
                out_dir: Path) -> dict:
    """Convenience wrapper: aggregate + checkpoint states for every root."""
    all_entries: list[dict] = []
    reports: list[dict] = []
    for root in eval_roots:
        model = model_by_root.get(root.name, {})

        def loader(r: Path = root) -> list[dict]:
            graph = QCGraph(db_path=r / "eval_checkpoint.sqlite3")
            try:
                return load_all_states(graph, r / "eval_checkpoint.sqlite3")
            finally:
                graph.close()

        entries, report = build_failure_pool(root, model, states_loader=loader)
        all_entries += entries
        reports.append(report)
    return write_pool(all_entries, reports, out_dir)


def load_all_states(graph: QCGraph, db_path: Path) -> list[dict]:
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    threads = [r[0] for r in conn.execute(
        "SELECT DISTINCT thread_id FROM checkpoints")]
    conn.close()
    states = []
    for thread_id in threads:
        values = graph.get_state(thread_id)
        if values:
            states.append(values)
    return states
