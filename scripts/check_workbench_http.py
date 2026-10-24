"""Isolated HTTP acceptance with scripted responses; not real browser click QA."""

import json
import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pico.providers.clients import FakeModelClient
from pico.runtime import Pico, SessionStore
from pico.web import Workbench, make_server
from pico.workspace import WorkspaceContext


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Optional project-relative acceptance report")
    options = parser.parse_args()
    cases = []
    with tempfile.TemporaryDirectory(prefix="codetrail-http-acceptance-") as directory:
        root = Path(directory)
        (root / "note.txt").write_text("original\n", encoding="utf-8")
        mode = ["allow"]

        class TimeoutClient(FakeModelClient):
            def complete(self, *args, **kwargs):
                raise TimeoutError("QA simulated timeout")

        def factory(session_id, policy, max_steps):
            outputs = {
                "allow": ['<tool>{"name":"patch_file","args":{"path":"note.txt","old_text":"original","new_text":"approved"}}</tool>', '<final>Updated.</final>'],
                "deny": ['<tool>{"name":"write_file","args":{"path":"blocked.txt","content":"must not exist"}}</tool>', '<final>Denied.</final>'],
                "resume": ['<final>Continued existing session.</final>'],
                "budget": ['<tool>{"name":"read_file","args":{"path":"note.txt"}}</tool>'] * 3,
            }
            client = TimeoutClient([]) if mode[0] == "timeout" else FakeModelClient(outputs[mode[0]])
            options = dict(model_client=client, workspace=WorkspaceContext.build(root, repo_root_override=root),
                           session_store=SessionStore(root / ".pico/sessions"), approval_policy=policy,
                           read_only=policy == "never", max_steps=max_steps)
            return Pico.from_session(session_id=session_id, **options) if session_id else Pico(**options)

        app = Workbench(root, factory=factory)
        app.model = FakeModelClient([])
        app.model.model = "scripted-acceptance-client"
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def api(path, data=None):
            headers = {"Content-Type": "application/json", "X-Pico-Token": app.csrf}
            request = urllib.request.Request(base + path, headers=headers,
                                             data=json.dumps(data).encode() if data is not None else None)
            return json.load(opener.open(request, timeout=5))

        def wait(task_id, predicate):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                job = api("/api/tasks/" + task_id)
                if predicate(job):
                    return job
                time.sleep(.02)
            raise AssertionError("Acceptance timed out")

        def finished(task_id):
            return wait(task_id, lambda j: j["status"] in {"completed", "failed", "stopped"})

        try:
            assert "CodeTrail" in opener.open(base).read().decode("utf-8")
            for path in ("/app.js", "/style.css"):
                assert opener.open(base + path).status == 200
            cases.append("页面与静态资源")
            task = api("/api/tasks", {"prompt": "Approve one patch", "policy": "ask"})
            pending = wait(task["id"], lambda j: j["approval"] is not None)
            assert (root / "note.txt").read_text(encoding="utf-8") == "original\n"
            assert api("/api/config")["active_id"] == task["id"]
            assert api("/api/tasks/" + task["id"])["approval"]["id"] == pending["approval"]["id"]
            cases.append("页面重连所需的活动任务和待审批状态可重新获取（API层）")
            try:
                api("/api/tasks", {"prompt": "Concurrent task"})
            except urllib.error.HTTPError as exc:
                assert exc.code == 409
            else:
                raise AssertionError("Concurrent request accepted")
            cases.append("并发任务拒绝")
            api(f"/api/tasks/{task['id']}/approvals/{pending['approval']['id']}", {"allowed": True})
            result = finished(task["id"])
            assert result["status"] == "completed" and result["diffs"][0]["path"] == "note.txt"
            assert (root / "note.txt").read_text(encoding="utf-8") == "approved\n"
            cases.append("允许审批后写入和差异")
            saved = api("/api/runs/" + result["run_id"])
            restored = Workbench(root).run(result["run_id"])
            assert saved["diffs"] == restored["diffs"] == result["diffs"]
            assert restored["session_id"] == result["session_id"]
            assert api("/api/runs")[0]["id"] == result["run_id"]
            assert any(s["id"] == result["session_id"] for s in api("/api/sessions"))
            cases.append("历史运行及重新加载持久化状态")
            mode[0] = "resume"
            continued = finished(api("/api/tasks", {"prompt": "Continue", "session_id": result["session_id"]})["id"])
            assert continued["session_id"] == result["session_id"]
            assert len(api("/api/sessions/" + result["session_id"])["messages"]) == 4
            cases.append("继续同一会话")
            mode[0] = "deny"
            denied_id = api("/api/tasks", {"prompt": "Deny write", "policy": "ask"})["id"]
            pending = wait(denied_id, lambda j: j["approval"] is not None)
            api(f"/api/tasks/{denied_id}/approvals/{pending['approval']['id']}", {"allowed": False})
            denied = finished(denied_id)
            assert not (root / "blocked.txt").exists()
            assert any(e.get("tool_error_code") == "approval_denied" for e in denied["events"])
            cases.append("拒绝审批不落盘")
            readonly = finished(api("/api/tasks", {"prompt": "Readonly", "policy": "never"})["id"])
            assert not (root / "blocked.txt").exists() and readonly["approval"] is None
            assert not any(e["event"] == "approval_requested" for e in readonly["events"])
            cases.append("只读模式拦截写入")
            mode[0] = "timeout"
            failed = finished(api("/api/tasks", {"prompt": "Timeout"})["id"])
            assert failed["status"] == "failed" and failed["outcome"]["code"] == "model_timeout"
            assert api("/api/runs/" + failed["run_id"])["outcome"]["code"] == "model_timeout"
            cases.append("超时原因实时与历史一致")
            mode[0] = "budget"
            stopped = finished(api("/api/tasks", {"prompt": "Budget", "max_steps": 1})["id"])
            assert stopped["status"] == "stopped" and stopped["outcome"]["code"] == "step_limit"
            assert api("/api/config")["active_id"] is None
            cases.append("步数耗尽与执行槽释放")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    result = {"kind": "scripted_HTTP_acceptance_not_browser_QA", "captured_at": datetime.now(timezone.utc).isoformat(),
              "passed": len(cases), "cases": cases, "browser_click_qa": "Separate acceptance; see workbench-browser-acceptance-2026-10-24.json",
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ["pico/web.py", "pico/web_assets/app.js", "pico/web_assets/index.html"]}}
    if options.output:
        target = (ROOT / options.output).resolve()
        if not target.is_relative_to(ROOT):
            parser.error("output must stay inside project")
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
