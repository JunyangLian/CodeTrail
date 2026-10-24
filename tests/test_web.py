import json
import threading
import urllib.error
import urllib.request

import pytest

from pico.providers.clients import FakeModelClient
from pico.runtime import Pico, SessionStore
from pico.web import RequestError, Workbench, make_server
from pico.workspace import WorkspaceContext


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    workspace = WorkspaceContext(str(tmp_path), str(tmp_path), "main", "main", "clean", [], {})
    monkeypatch.setattr(WorkspaceContext, "build", lambda *args, **kwargs: workspace)
    monkeypatch.setenv("PICO_PROVIDER", "openai")
    monkeypatch.setenv("PICO_OPENAI_API_KEY", "web-test-private-key")

    def create(outputs):
        def factory(session_id, policy, max_steps):
            options = dict(
                model_client=FakeModelClient(list(outputs)), workspace=workspace,
                session_store=SessionStore(tmp_path / ".pico/sessions"),
                approval_policy=policy, max_steps=max_steps, read_only=policy == "never",
            )
            return Pico.from_session(session_id=session_id, **options) if session_id else Pico(**options)
        return Workbench(tmp_path, factory=factory)
    return create


def finish(task):
    with task.condition:
        assert task.condition.wait_for(lambda: task.finished is not None, timeout=10)
    return task.view()


def test_task_records_real_events_diffs_and_persistent_history(make_app, tmp_path):
    (tmp_path / "app.py").write_text("answer = 1\n", encoding="utf-8")
    app = make_app([
        '<tool>{"name":"patch_file","args":{"path":"app.py","old_text":"answer = 1","new_text":"answer = 2"}}</tool>',
        "<final>Updated.</final>",
    ])
    submitted = app.submit({"prompt": "Fix answer", "policy": "auto"})
    result = finish(app.task(submitted["id"]))

    assert result["status"] == "completed"
    assert result["tool_steps"] == 1
    assert "+answer = 2" in result["diffs"][0]["diff"]
    assert any(event["event"] == "tool_executed" for event in result["events"])
    assert app.active_id is None
    restored = Workbench(tmp_path).run(result["run_id"])
    assert restored["diffs"] == result["diffs"]
    assert restored["session_id"] == result["session_id"]
    assert app.session(result["session_id"])["messages"][-1]["content"] == "Updated."
    assert app.runs()[0]["status"] == "completed"


def test_approval_is_bound_to_task_and_cannot_be_replayed(make_app, tmp_path):
    app = make_app([
        '<tool>{"name":"write_file","args":{"path":"note.txt","content":"approved"}}</tool>',
        "<final>Done.</final>",
    ])
    task = app.task(app.submit({"prompt": "Write note"})["id"])
    with task.condition:
        assert task.condition.wait_for(lambda: task.approval is not None, timeout=10)
        approval_id = task.approval["id"]
    assert not (tmp_path / "note.txt").exists()
    with pytest.raises(RequestError, match="已有任务"):
        app.submit({"prompt": "Concurrent write"})
    with pytest.raises(RequestError, match="审批"):
        app.approve(task.id, "wrong-id", True)
    app.approve(task.id, approval_id, True)
    assert finish(task)["status"] == "completed"
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "approved"
    with pytest.raises(RequestError, match="审批"):
        app.approve(task.id, approval_id, True)


def test_denied_and_readonly_writes_do_not_modify_files(make_app, tmp_path):
    output = '<tool>{"name":"write_file","args":{"path":"blocked.txt","content":"no"}}</tool>'
    app = make_app([output, "<final>Denied.</final>"])
    task = app.task(app.submit({"prompt": "Write", "policy": "ask"})["id"])
    with task.condition:
        assert task.condition.wait_for(lambda: task.approval is not None, timeout=10)
        approval_id = task.approval["id"]
    app.approve(task.id, approval_id, False)
    finish(task)
    task2 = app.task(app.submit({"prompt": "Write", "policy": "never"})["id"])
    result = finish(task2)
    assert not (tmp_path / "blocked.txt").exists()
    assert not any(e["event"] == "approval_requested" for e in result["events"])


def test_model_failure_keeps_error_and_releases_worker(make_app):
    app = make_app([])
    original = app.factory

    class BrokenClient:
        def complete(self, *args, **kwargs):
            raise RuntimeError("network failed: web-test-private-key")

    def factory(*args):
        agent = original(*args)
        agent.model_client = BrokenClient()
        return agent
    app.factory = factory
    result = finish(app.task(app.submit({"prompt": "Explain"})["id"]))
    assert result["status"] == "failed"
    assert "web-test-private-key" not in json.dumps(result)
    assert app.active_id is None


def test_resume_reuses_session(make_app):
    app = make_app(["<final>First.</final>"])
    first = finish(app.task(app.submit({"prompt": "Remember context"})["id"]))
    second = finish(app.task(app.submit({"prompt": "Continue", "session_id": first["session_id"]})["id"]))
    assert second["session_id"] == first["session_id"]
    assert len(app.session(first["session_id"])["messages"]) == 4


@pytest.mark.parametrize("exit_code,expected", [(0, "verified"), (3, "verification_failed")])
def test_verification_uses_exit_code_and_survives_restart(make_app, tmp_path, exit_code, expected):
    command = f'python -c "raise SystemExit({exit_code})"'
    app = make_app([
        "<tool>" + json.dumps({"name": "run_shell", "args": {"command": command}}) + "</tool>",
        "<final>All done.</final>",
    ])
    app.verification_command = command
    result = finish(app.task(app.submit({"prompt": "Verify", "policy": "auto"})["id"]))
    assert result["status"] == "completed"
    assert result["outcome"]["code"] == expected
    assert result["verification"]["exit_code"] == exit_code
    assert next(e for e in result["events"] if e["event"] == "tool_executed")["exit_code"] == exit_code
    restored = Workbench(tmp_path).run(result["run_id"])
    assert restored["outcome"] == result["outcome"]
    assert restored["verification"] == result["verification"]
    assert app.runs()[0]["outcome"]["code"] == expected


def test_step_limit_remains_stopped_and_releases_worker(make_app, tmp_path):
    (tmp_path / "note.txt").write_text("hi", encoding="utf-8")
    output = '<tool>{"name":"read_file","args":{"path":"note.txt"}}</tool>'
    app = make_app([output, output])
    result = finish(app.task(app.submit({"prompt": "Read", "max_steps": 1})["id"]))
    assert result["status"] == "stopped"
    assert result["outcome"]["code"] == "step_limit"
    assert app.active_id is None
    assert Workbench(tmp_path).run(result["run_id"])["outcome"] == result["outcome"]


@pytest.mark.parametrize("value", [0, 301, True])
def test_invalid_model_timeout_is_rejected_before_starting(tmp_path, value):
    with pytest.raises(ValueError, match="超时"):
        Workbench(tmp_path, model_timeout=value)


@pytest.mark.parametrize("data", [
    {"prompt": ""}, {"prompt": "ok", "max_steps": 0}, {"prompt": "ok", "max_steps": True},
    {"prompt": "ok", "policy": "invalid"}, {"prompt": "ok", "session_id": "../secret"},
])
def test_invalid_submissions_are_rejected(make_app, data):
    with pytest.raises(RequestError):
        make_app([]).submit(data)


def test_http_assets_and_mutation_protection(make_app):
    app = make_app(["<final>ok</final>"])
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        config = json.loads(opener.open(url + "/api/config").read())
        assert "web-test-private-key" not in json.dumps(config)
        assert "CodeTrail · 代码执行工作台" in opener.open(url).read().decode("utf-8")
        assert opener.open(url + "/app.js").status == 200
        assert opener.open(url + "/style.css").status == 200
        for path in ("/.env", "/api/sessions/..%2fsecret"):
            with pytest.raises(urllib.error.HTTPError):
                opener.open(url + path)
        for headers in ({}, {"X-Pico-Token": config["csrf"], "Origin": "https://untrusted.example"}):
            request = urllib.request.Request(url + "/api/tasks", data=b'{"prompt":"test"}', headers=headers)
            with pytest.raises(urllib.error.HTTPError) as exc:
                opener.open(request)
            assert exc.value.code == 403
        request = urllib.request.Request(url + "/api/tasks", data=b'{"prompt":"test"}', headers={"X-Pico-Token": config["csrf"]})
        result = json.loads(opener.open(request).read())
        assert finish(app.task(result["id"]))["status"] == "completed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
