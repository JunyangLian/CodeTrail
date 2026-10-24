"""Independently recheck all original and post-feedback repair runs, preserving failures."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_repair_validation import file_inventory, verify


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def batch_audit(folder, manifest):
    source = ROOT / "artifacts" / folder / "results.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    assert data["completed"] and data["manifest_sha256"] == digest(ROOT / "benchmarks/repair_validation_tasks.json")
    tasks = {task["id"]: task for task in manifest["tasks"]}
    rows = []
    seen = set()
    for row in data["rows"]:
        assert (row["task_id"], row["repeat"]) not in seen
        seen.add((row["task_id"], row["repeat"]))
        task = tasks[row["task_id"]]
        workspace = ROOT / row["workspace"]
        initial_manifest = {name: hashlib.sha256(content.encode("utf-8")).hexdigest() for name, content in task["files"].items()}
        assert row["initial_sha256"] == initial_manifest
        assert file_inventory(workspace) == row["final_sha256"]
        assert digest(ROOT / row["trace"]) == row["trace_sha256"]
        recheck = verify(workspace)
        assert recheck["test_count"] == task["expected_tests"]
        assert recheck["exit_code"] == row["final_verifier"]["exit_code"]
        original_initial = row["initial_verifier"]
        assert original_initial["exit_code"] != 0 and original_initial["test_count"] == task["expected_tests"]
        functional = recheck["exit_code"] == 0 and row["protected_files_unchanged"] and row["change_scope_valid"]
        workflow = bool(functional and row.get("status") == "completed" and row.get("model_verifier_exit_codes", [None])[-1:] == [0])
        assert workflow == row["passed"]
        category = ("" if workflow else "no_code_change" if not row["changed_files"] else
                    "missing_agent_reverification" if functional else row["failure_category"])
        events = [json.loads(line) for line in (ROOT / row["trace"]).read_text(encoding="utf-8").splitlines()]
        tool_failures = []
        for index, event in enumerate(events, 1):
            if event["event"] == "tool_executed" and event.get("tool_status") != "ok":
                tool_failures.append({"trace_line": index, "name": event.get("name"), "code": event.get("tool_error_code"),
                                      "exit_code": event.get("exit_code"), "argument_fields": list(event.get("args", {}))})
        rows.append({"task_id": row["task_id"], "repeat": row["repeat"], "run_id": row["run_id"],
                     "workflow_passed": workflow, "functional_verifier_passed": functional,
                     "classification": category, "raw_failure_category": row["failure_category"],
                     "initial_verifier": original_initial, "independent_final_recheck": recheck,
                     "agent_verifier_exit_codes": row.get("model_verifier_exit_codes"),
                     "protected_files_unchanged": row["protected_files_unchanged"], "changed_files": row["changed_files"],
                     "attempts": row.get("attempts"), "tool_steps": row.get("tool_steps"), "elapsed_ms": row["elapsed_ms"],
                     "observed_input_tokens": row.get("observed_input_tokens"), "observed_output_tokens": row.get("observed_output_tokens"),
                     "tool_issues": tool_failures, "trace": row["trace"], "trace_sha256": row["trace_sha256"],
                     "initial_sha256": row["initial_sha256"], "final_sha256": row["final_sha256"], "diffs": row["diffs"]})
    assert len(rows) == data["planned_runs"]
    return {"source": source.relative_to(ROOT).as_posix(), "source_sha256": digest(source), "configuration": data["configuration"],
            "runtime_sha256": data.get("runtime_sha256"), "scope": data["scope"], "unique_tasks": data["planned_tasks"],
            "runs": len(rows), "workflow_passed": sum(r["workflow_passed"] for r in rows),
            "functional_verifier_passed": sum(r["functional_verifier_passed"] for r in rows),
            "failure_categories": dict(Counter(r["classification"] for r in rows if r["classification"])), "rows": rows}


def main():
    manifest_path = ROOT / "benchmarks/repair_validation_tasks.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    baseline = batch_audit("repair-validation-2026-10-24", manifest)
    baseline["runtime_fingerprints"] = json.loads((ROOT / "docs/repair-baseline-source-2026-10-24.json").read_text(encoding="utf-8"))
    after = batch_audit("repair-feedback-regression-2026-10-24", manifest)
    payload = {"schema_version": 1, "audited_at": datetime.now(timezone.utc).isoformat(),
               "manifest_sha256": digest(manifest_path), "task_manifest": manifest,
               "baseline": baseline, "post_feedback_targeted_regression": after,
               "boundary": "Two author-created tasks with two baseline runs each. One additional post-change regression is separate, not substituted into the baseline. This is not a public benchmark or stable success-rate estimate.",
               "metric_definitions": {"functional_verifier_passed": "Pristine test hashes unchanged, scope valid, external verifier exits zero with all expected tests.",
                                      "workflow_passed": "Functional pass plus completed run plus a final successful verifier call observed in the agent trace."},
               "feedback_change": "Invalid patch_file arguments stay rejected; feedback provides separate JSON fields and an escaped-string example. No automatic repair or execution of malformed calls.",
               "causal_limit": "Post-change sample n=1. Error feedback was exercised once and recovery observed, but this does not establish a statistically reliable success-rate improvement."}
    target = ROOT / "docs/repair-validation-2026-10-24.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"baseline": {k: baseline[k] for k in ["runs", "workflow_passed", "functional_verifier_passed"]},
                      "post_feedback": {k: after[k] for k in ["runs", "workflow_passed", "functional_verifier_passed"]}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
