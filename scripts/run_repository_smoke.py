"""Real-model smoke test: 3 frozen author-created reading tasks x 2 fresh runs."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pico.cli import _build_model_client, _configured_secret_names, build_arg_parser
from pico.config import load_project_env
from pico.runtime import Pico, SessionStore
from pico.security import redact_artifact
from pico.workspace import WorkspaceContext


def write(path, payload):
    path.write_text(json.dumps(redact_artifact(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts/repository-smoke-2026-10-24")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT):
        parser.error("output must stay inside project")
    output.mkdir(parents=True, exist_ok=False)
    manifest_path = ROOT / "benchmarks/repository_smoke_tasks.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    load_project_env(ROOT)
    client_args = build_arg_parser().parse_args(["--cwd", str(ROOT), "--openai-timeout", str(args.timeout)])
    payload = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
               "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
               "task_manifest": manifest, "task_count": len(manifest["tasks"]), "repetitions": 2,
               "planned_runs": len(manifest["tasks"]) * 2, "completed": False,
               "scope": "Author-created reading-only smoke test, fresh workspace/session per run; no prompt edits or reruns after seeing results; no repair/generalization/model ranking claim.",
               "configuration": {"temperature": client_args.temperature, "timeout_seconds": args.timeout, "max_steps": 2, "max_new_tokens": 256,
                                 "allowed_tools": ["read_file"], "approval": "never", "read_only": True}, "rows": []}
    write(output / "results.json", payload)
    for task in manifest["tasks"]:
        for repeat in range(1, 3):
            workspace = output / f"{task['id']}-{repeat}"
            workspace.mkdir()
            fixture = workspace / task["filename"]
            fixture.write_text(task["content"], encoding="utf-8")
            before = hashlib.sha256(fixture.read_bytes()).hexdigest()
            row = {"task_id": task["id"], "repeat": repeat, "category": task["category"], "fixture_sha256": before,
                   "started_at": datetime.now(timezone.utc).isoformat(), "passed": False}
            started = time.monotonic()
            agent = None
            try:
                client = _build_model_client(client_args)
                payload["configuration"]["model"] = client.model
                agent = Pico(model_client=client, workspace=WorkspaceContext.build(workspace, repo_root_override=workspace),
                             session_store=SessionStore(workspace / ".pico/sessions"), approval_policy="never", read_only=True,
                             allowed_tools=["read_file"], max_steps=2, max_new_tokens=256,
                             secret_env_names=_configured_secret_names(client_args))
                answer = agent.ask(task["prompt"])
                row["answer"] = answer
                try:
                    actual = json.loads(answer)
                except ValueError:
                    row["failure_category"] = "answer_format"
                else:
                    row["exact_match"] = actual == task["expected"]
                    row["failure_category"] = "" if row["exact_match"] else "answer_mismatch"
            except Exception as exc:
                row["failure_category"] = "model_or_runtime_error"
                row["error"] = str(exc)
            state = getattr(agent, "current_task_state", None)
            if state:
                row.update(run_id=state.run_id, status=state.status, stop_reason=state.stop_reason,
                           attempts=state.attempts, tool_steps=state.tool_steps)
                trace_path = workspace / ".pico/runs" / state.run_id / "trace.jsonl"
                trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
                row["read_observed"] = any(e.get("event") == "tool_executed" and e.get("name") == "read_file" and e.get("args", {}).get("path") == task["filename"] and e.get("tool_status") == "ok" for e in trace)
                parsed = [e for e in trace if e.get("event") == "model_parsed"]
                row["observed_input_tokens"] = sum(e.get("completion_metadata", {}).get("input_tokens", 0) or 0 for e in parsed)
                row["observed_output_tokens"] = sum(e.get("completion_metadata", {}).get("output_tokens", 0) or 0 for e in parsed)
                row["trace"] = trace_path.relative_to(ROOT).as_posix()
            row["fixture_unchanged"] = hashlib.sha256(fixture.read_bytes()).hexdigest() == before
            row["passed"] = bool(row.get("exact_match") and row.get("read_observed") and row["fixture_unchanged"] and row.get("status") == "completed")
            if not row["passed"] and not row.get("failure_category"):
                row["failure_category"] = "missing_read_or_incomplete"
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            payload["rows"].append(row)
            write(output / "results.json", payload)
            print(json.dumps({k: row.get(k) for k in ["task_id", "repeat", "passed", "failure_category", "elapsed_ms"]}), flush=True)
    payload["completed"] = True
    payload["finished_at"] = datetime.now(timezone.utc).isoformat()
    payload["summary"] = {"passed": sum(row["passed"] for row in payload["rows"]), "total_runs": len(payload["rows"]),
                          "unique_tasks": len(manifest["tasks"]), "usage_scope": "Parsed responses only; no price/cost estimate."}
    write(output / "results.json", payload)
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == payload["manifest_sha256"]
    print(json.dumps(payload["summary"]), flush=True)


if __name__ == "__main__":
    main()
