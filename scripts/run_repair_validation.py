"""Four frozen real-model repair runs, immutable tests and an external verifier."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pico.cli import _build_model_client, _configured_secret_names, build_arg_parser
from pico.config import load_project_env
from pico.runtime import DEFAULT_SHELL_ENV_ALLOWLIST, Pico, SessionStore
from pico.security import redact_artifact
from pico.web import make_diffs
from pico.workspace import WorkspaceContext

VERIFY_COMMAND = "python -B -m unittest discover -v"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(redact_artifact(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def install_fixture(path, task):
    path.mkdir()
    for name, content in task["files"].items():
        (path / name).write_text(content, encoding="utf-8", newline="\n")


def verify(path):
    started = time.monotonic()
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-v"], cwd=path,
                            capture_output=True, timeout=20, env=env)
    output = result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace")
    count = re.search(r"Ran (\d+) tests?", output)
    return {"exit_code": result.returncode, "test_count": int(count[1]) if count else None,
            "output": output, "elapsed_ms": round((time.monotonic() - started) * 1000)}


def preflight(output, tasks):
    rows = []
    for task in tasks:
        workspace = output / ("preflight-" + task["id"])
        install_fixture(workspace, task)
        initial = verify(workspace)
        assert initial["exit_code"] != 0 and initial["test_count"] == task["expected_tests"], task["id"]
        for edit in task["reference_edits"]:
            path = workspace / edit["path"]
            text = path.read_text(encoding="utf-8")
            assert text.count(edit["old"]) == 1
            path.write_text(text.replace(edit["old"], edit["new"]), encoding="utf-8", newline="\n")
        repaired = verify(workspace)
        assert repaired["exit_code"] == 0 and repaired["test_count"] == task["expected_tests"], repaired
        rows.append({"task_id": task["id"], "kind": "reference_solution_verifier_sanity_check_not_model_run",
                     "initial": initial, "reference_repair": repaired})
    return rows


def file_inventory(workspace):
    return {path.relative_to(workspace).as_posix(): digest(path) for path in workspace.rglob("*")
            if path.is_file() and not any(part in {".pico", "__pycache__"} for part in path.relative_to(workspace).parts)}


def execute(task, repeat, output, client_args):
    workspace = output / f"{task['id']}-{repeat}"
    install_fixture(workspace, task)
    before = file_inventory(workspace)
    initial = verify(workspace)
    row = {"task_id": task["id"], "repeat": repeat, "category": task["category"],
           "workspace": workspace.relative_to(ROOT).as_posix(), "initial_sha256": before,
           "initial_verifier": initial, "started_at": datetime.now(timezone.utc).isoformat(),
           "approvals": [], "passed": False}
    agent = None
    started = time.monotonic()
    try:
        agent = Pico(model_client=_build_model_client(client_args),
                     workspace=WorkspaceContext.build(workspace, repo_root_override=workspace),
                     session_store=SessionStore(workspace / ".pico/sessions"),
                     allowed_tools=["read_file", "patch_file", "write_file", "run_shell"],
                     approval_policy="ask", max_steps=10, max_new_tokens=2048,
                     shell_env_allowlist=DEFAULT_SHELL_ENV_ALLOWLIST + ("PYTHONDONTWRITEBYTECODE",),
                     secret_env_names=_configured_secret_names(client_args))

        def approve(name, args):
            allowed = (name == "run_shell" and args.get("command", "").strip() == VERIFY_COMMAND
                       or name in {"patch_file", "write_file"} and args.get("path", "") in task["editable_files"])
            row["approvals"].append({"name": name, "allowed": bool(allowed),
                                     "path": args.get("path"), "command": args.get("command")})
            return allowed

        agent.approve = approve
        row["answer"] = agent.ask(task["prompt"])
    except Exception as exc:
        row["error"] = str(exc)
    state = getattr(agent, "current_task_state", None)
    if state:
        row.update(run_id=state.run_id, status=state.status, stop_reason=state.stop_reason,
                   attempts=state.attempts, tool_steps=state.tool_steps)
        trace_path = workspace / ".pico/runs" / state.run_id / "trace.jsonl"
        events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        row["trace"] = trace_path.relative_to(ROOT).as_posix()
        row["trace_sha256"] = digest(trace_path)
        parsed = [e for e in events if e["event"] == "model_parsed"]
        row["observed_input_tokens"] = sum(e.get("completion_metadata", {}).get("input_tokens", 0) or 0 for e in parsed)
        row["observed_output_tokens"] = sum(e.get("completion_metadata", {}).get("output_tokens", 0) or 0 for e in parsed)
        row["tool_issues"] = [{"name": e.get("name"), "code": e.get("tool_error_code"), "exit_code": e.get("exit_code")}
                              for e in events if e["event"] == "tool_executed" and e.get("tool_status") != "ok"]
        row["model_verifier_exit_codes"] = [e.get("exit_code") for e in events if e["event"] == "tool_executed"
                                           and e.get("name") == "run_shell" and e.get("args", {}).get("command", "").strip() == VERIFY_COMMAND]
    after = file_inventory(workspace)
    row["final_sha256"] = after
    changed = [name for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
    row["changed_files"] = changed
    row["protected_files_unchanged"] = all(after.get(name) == value for name, value in before.items() if name not in task["editable_files"])
    row["change_scope_valid"] = bool(changed) and set(changed) <= set(task["editable_files"])
    row["diffs"] = make_diffs({name: task["files"][name] for name in task["editable_files"]},
                             {name: (workspace / name).read_text(encoding="utf-8") for name in task["editable_files"]})
    row["final_verifier"] = verify(workspace)
    final = row["final_verifier"]
    row["passed"] = bool(initial["exit_code"] != 0 and initial["test_count"] == task["expected_tests"]
                         and final["exit_code"] == 0 and final["test_count"] == task["expected_tests"]
                         and row["protected_files_unchanged"] and row["change_scope_valid"]
                         and row.get("status") == "completed" and row.get("model_verifier_exit_codes", [None])[-1:] == [0])
    row["failure_category"] = ("" if row["passed"] else "model_request_or_runtime" if row.get("error") else
                               "protected_file_or_scope" if not row["protected_files_unchanged"] or set(changed) - set(task["editable_files"]) else
                               "no_code_change" if not changed else
                               "verifier_failed" if final["exit_code"] != 0 else "incomplete_run_or_missing_model_verification")
    row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return row


def main():
    manifest_path = ROOT / "benchmarks/repair_validation_tasks.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts/repair-validation-2026-10-24")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--task", choices=[task["id"] for task in manifest["tasks"]], help="Explicit targeted regression; report separately from the full batch")
    parser.add_argument("--repetitions", type=int, choices=[1, 2, 3], default=2)
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT):
        parser.error("output must be inside project")
    output.mkdir(parents=True, exist_ok=False)
    manifest_hash = digest(manifest_path)
    tasks = [task for task in manifest["tasks"] if args.task is None or task["id"] == args.task]
    payload = {"schema_version": 1, "manifest_sha256": manifest_hash, "started_at": datetime.now(timezone.utc).isoformat(),
               "scope": "Targeted post-change regression; not added to original batch." if args.task else manifest["scope"],
               "planned_tasks": len(tasks), "repetitions": args.repetitions, "planned_runs": len(tasks) * args.repetitions,
               "runtime_sha256": {name: digest(ROOT / name) for name in ["pico/runtime.py", "pico/agent_loop.py", "pico/tool_executor.py", "pico/prompt_prefix.py"]},
               "completed": False, "preflight": preflight(output, tasks), "rows": []}
    save(output / "results.json", payload)
    if args.preflight_only:
        print("Both buggy fixtures fail and both reference repairs pass all 8 tests.", flush=True)
        return
    load_project_env(ROOT)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    client_args = build_arg_parser().parse_args(["--cwd", str(ROOT), "--openai-timeout", "120"])
    model = _build_model_client(client_args)
    payload["configuration"] = {"model": model.model, "temperature": client_args.temperature, "timeout_seconds": 120,
                                "max_steps": 10, "max_new_tokens": 2048, "verify_command": VERIFY_COMMAND,
                                "notes": "Fresh workspace and session; immutable tests; only listed implementation files can be edited; shell approval only for exact verifier; no prompt edits, success-only filtering, or additional reruns."}
    for task in tasks:
        for repeat in range(1, args.repetitions + 1):
            print(f"Starting {task['id']} repeat {repeat}", flush=True)
            row = execute(task, repeat, output, client_args)
            payload["rows"].append(row)
            save(output / "results.json", payload)
            print(json.dumps({key: row[key] for key in ["task_id", "repeat", "passed", "failure_category", "elapsed_ms"]}), flush=True)
    assert digest(manifest_path) == manifest_hash
    payload["completed"] = True
    payload["finished_at"] = datetime.now(timezone.utc).isoformat()
    payload["summary"] = {"unique_tasks": len(tasks), "runs": len(payload["rows"]), "passed": sum(row["passed"] for row in payload["rows"]),
                          "failure_categories": dict(Counter(row["failure_category"] for row in payload["rows"] if row["failure_category"]))}
    save(output / "results.json", payload)
    print(json.dumps(payload["summary"]), flush=True)


if __name__ == "__main__":
    main()
