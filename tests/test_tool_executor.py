from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.tool_executor import ToolExecutor, ToolExecutionResult
import subprocess
import json


def build_agent(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".pico" / "sessions")
    return Pico(
        model_client=FakeModelClient([]),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
    )


def test_tool_executor_returns_content_and_metadata_without_side_channel(tmp_path):
    agent = build_agent(tmp_path)

    result = ToolExecutor(agent).execute("read_file", {"path": "README.md", "start": 1, "end": 1})

    assert isinstance(result, ToolExecutionResult)
    assert "# README.md" in result.content
    assert result.metadata["tool_status"] == "ok"
    assert result.metadata["read_only"] is True
    assert result.metadata["workspace_changed"] is False


def test_pico_run_tool_keeps_compatibility_metadata(tmp_path):
    agent = build_agent(tmp_path)

    content = agent.run_tool("read_file", {"path": "README.md", "start": 1, "end": 1})

    assert "# README.md" in content
    assert agent._last_tool_result_metadata["tool_status"] == "ok"


def test_shell_timeout_has_structured_reason(tmp_path, monkeypatch):
    agent = build_agent(tmp_path)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("python -V", 1)

    monkeypatch.setitem(agent.tools["run_shell"], "run", timeout)
    result = ToolExecutor(agent).execute("run_shell", {"command": "python -V", "timeout": 1})
    assert result.metadata["tool_error_code"] == "tool_timeout"
    assert result.metadata["timed_out"] is True
    assert result.metadata["tool_status"] == "error"


def test_missing_patch_field_returns_json_repair_example_without_writing(tmp_path):
    agent = build_agent(tmp_path)
    (tmp_path / "app.py").write_text("answer = 1\n", encoding="utf-8")
    result = agent.execute_tool("patch_file", {
        "path": "app.py", "old_text": "answer = 1</new_text>answer = 2",
    })
    assert result.metadata["tool_error_code"] == "invalid_arguments"
    assert "Keep old_text and new_text as separate JSON fields" in result.content
    example = result.content.split("example: <tool>", 1)[1].split("</tool>", 1)[0]
    payload = json.loads(example)
    assert payload["name"] == "patch_file"
    assert set(payload["args"]) == {"path", "old_text", "new_text"}
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "answer = 1\n"


def test_valid_patch_after_rejection_still_uses_normal_execution(tmp_path):
    agent = build_agent(tmp_path)
    (tmp_path / "app.py").write_text("answer = 1\n", encoding="utf-8")
    rejected = agent.execute_tool("patch_file", {"path": "app.py", "old_text": "answer = 1"})
    assert rejected.metadata["tool_error_code"] == "invalid_arguments"
    corrected = agent.execute_tool("patch_file", {"path": "app.py", "old_text": "answer = 1", "new_text": "answer = 2"})
    assert corrected.metadata["tool_status"] == "ok"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "answer = 2\n"
