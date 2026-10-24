"""Reconcile historical metrics and real traces without rerunning historical experiments."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pico.security import redact_artifact


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def smoke_audit():
    results_path = ROOT / "artifacts/repository-smoke-2026-10-24/results.json"
    if not results_path.exists():
        return {"available": False}
    data = read(results_path)
    manifest_path = ROOT / "benchmarks/repository_smoke_tasks.json"
    manifest = read(manifest_path)
    assert digest(manifest_path) == data["manifest_sha256"], "Task manifest changed after experiment"
    assert data["completed"] and len(data["rows"]) == len(manifest["tasks"]) * 2
    tasks = {task["id"]: task for task in manifest["tasks"]}
    seen = set()
    checks = []
    for row in data["rows"]:
        key = row["task_id"], row["repeat"]
        assert key not in seen and row["repeat"] in (1, 2)
        seen.add(key)
        task = tasks[row["task_id"]]
        trace_path = ROOT / row["trace"] if row.get("trace") else None
        events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()] if trace_path else []
        workspace = results_path.parent / f"{row['task_id']}-{row['repeat']}"
        fixture_path = workspace / task["filename"]
        # Windows text-mode fixture creation uses CRLF. Check the original byte
        # hash for immutability and normalized text separately for manifest parity.
        fixture_unchanged = (digest(fixture_path) == row["fixture_sha256"]
                             and fixture_path.read_text(encoding="utf-8") == task["content"])
        tools = [e for e in events if e.get("event") == "tool_executed"]
        try:
            answer_correct = json.loads(row.get("answer", "")) == task["expected"]
        except ValueError:
            answer_correct = False
        read_observed = any(e.get("name") == "read_file" and e.get("args", {}).get("path") == task["filename"] and e.get("tool_status") == "ok" for e in tools)
        allowed_only = all(e.get("name") == "read_file" for e in tools)
        finished = next((e for e in reversed(events) if e["event"] == "run_finished"), {})
        passed = bool(answer_correct and read_observed and allowed_only and fixture_unchanged and finished.get("status") == "completed")
        assert passed == row["passed"], "Result and independent evidence audit disagree"
        checks.append({"task_id": row["task_id"], "repeat": row["repeat"], "passed": passed,
                       "read_observed": read_observed, "allowed_tools_only": allowed_only, "fixture_unchanged": fixture_unchanged,
                       "elapsed_ms": row["elapsed_ms"], "attempts": row.get("attempts"), "tool_steps": row.get("tool_steps"),
                       "trace_sha256": digest(trace_path) if trace_path else None, "run_id": row.get("run_id"),
                       "failure_category": row.get("failure_category"),
                       "observed_input_tokens": row.get("observed_input_tokens"), "observed_output_tokens": row.get("observed_output_tokens")})
    return {"available": True, "source": results_path.relative_to(ROOT).as_posix(), "source_sha256": digest(results_path),
            "manifest_sha256": data["manifest_sha256"], "configuration": data["configuration"],
            "unique_tasks": len(tasks), "runs": len(checks), "passed": sum(item["passed"] for item in checks),
            "verification": "Freshly checked answers, trace reads, tool allowlist, final status and fixture hashes; no model calls during audit.",
            "scope": data["scope"], "rows": checks}


def evidence_audit():
    archive = ROOT / "benchmarks/results/main-resume-repro-2026-06-07"
    context = read(archive / "context-ablation-v2.json")
    memory = read(archive / "memory-ablation-v2.json")
    harness = read(archive / "harness-regression-v2.json")
    demo = read(ROOT / "docs/order-demo-run-2026-10-23.json")
    runs = []
    for run_id in [*demo["earlier_timeout_runs"], demo["run_id"]]:
        directory = ROOT / "examples/interview-demo/.pico/runs" / run_id
        if not (directory / "report.json").exists():
            runs.append({"run_id": run_id, "available": False})
            continue
        report = read(directory / "report.json")
        events = [json.loads(line) for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        issues = []
        for line_number, event in enumerate(events, 1):
            if event.get("event") == "model_failed" or (event.get("event") == "tool_executed" and event.get("tool_status") != "ok"):
                code = event.get("tool_error_code") or "model_error"
                error_text = event.get("error", "")
                category = ("model_timeout" if "timed out" in error_text.lower() else "model_request_error") if event["event"] == "model_failed" else (
                    "invalid_tool_arguments" if code == "invalid_arguments" else
                    "initial_fixture_test_failure" if event.get("args", {}).get("command") == demo["verification_command"] and event.get("exit_code", 1) == 1 else "tool_execution_error")
                issues.append({"trace_line": line_number, "category": category, "event": event["event"],
                               "tool": event.get("name"), "args": event.get("args"), "error_code": code,
                               "error": error_text, "exit_code": event.get("exit_code"),
                               "evidence_excerpt": event.get("result", "")[:350]})
        parsed = [e for e in events if e.get("event") == "model_parsed"]
        duration = next((e.get("run_duration_ms") for e in reversed(events) if e["event"] == "run_finished"), None)
        runs.append({"run_id": run_id, "available": True, "status": report["status"], "stop_reason": report["stop_reason"],
                     "attempts": report["attempts"], "tool_steps": report["tool_steps"], "duration_ms": duration,
                     "observed_input_tokens": sum((e.get("completion_metadata", {}).get("input_tokens") or 0) for e in parsed),
                     "observed_output_tokens": sum((e.get("completion_metadata", {}).get("output_tokens") or 0) for e in parsed),
                     "usage_scope": "Successful parsed responses only; failed requests may have unreported provider usage. No monetary cost estimate.",
                     "trace_sha256": digest(directory / "trace.jsonl"), "report_sha256": digest(directory / "report.json"), "issues": issues})
    category_counts = Counter(issue["category"] for run in runs for issue in run.get("issues", []))
    metric_rows = {
        "harness": {"kind": "scripted_deterministic_regression", "task_count": harness["benchmark"]["task_count"],
                    "model": harness["reproducibility"]["model_name"], "summary": harness.get("summary"),
                    "claim_boundary": "Verifies Harness contracts; not real-model task success."},
        "context": {"kind": "constructed_context_matrix", "config_count": context["config_count"], "summary": context["summary"],
                    "unit": "characters, not tokens", "claim_boundary": "Historical snapshot; not rerun on current source; no task-success inference."},
        "memory": {"kind": "scripted_controlled_followup", "task_count": memory["task_count"],
                   "runs_per_variant": memory["runs_per_variant"], "variants": memory["variants"],
                   "metric_scope": "Second ask() only; bootstrap read excluded; repeated_reads counts all read_file calls in follow-up.",
                   "claim_boundary": "Scripted client; 5 deterministic repetitions are not 5 independent model samples."},
        "real_order_demo": {"kind": "real_model_development_walkthrough", "summary": demo,
                            "claim_boundary": "One development task with retries; 15 tests are assertions, not 15 independent agent tasks."},
    }
    return redact_artifact({"schema_version": 1, "audited_at": datetime.now(timezone.utc).isoformat(),
        "method": "Read-only reconciliation; archived experiments not rerun.", "metrics": metric_rows,
        "archived_source_sha256": {p.name: digest(p) for p in archive.glob("*.json")},
        "current_source_sha256": {name: digest(ROOT / name) for name in ["pico/features/memory.py", "pico/evaluation/metrics.py", "pico/agent_loop.py", "pico/web_status.py"]},
        "unverified_resume_claims": ["6964 -> 5418 / 18.01%", "8 -> 3 / 0.67 -> 0.25 / 66.7% -> 100%", "GPT 5/6 / 83.33%", "86 automated tests as current total"],
        "unverified_claim_action": "Do not use as current personally reproduced results; historical origin must be recovered before reuse.",
        "real_runs": runs, "issue_category_counts": dict(category_counts), "repeated_smoke": smoke_audit(),
        "causal_limit": "Timeout cause unconfirmed; prompt, session and timeout changed together. Later success is not a controlled causal comparison."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="docs/evidence-audit-2026-10-24.json")
    args = parser.parse_args()
    target = ROOT / args.output
    payload = evidence_audit()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(target), "real_runs": len(payload["real_runs"]), "issues": payload["issue_category_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
