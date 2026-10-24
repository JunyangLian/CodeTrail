import os
import shlex
import subprocess
import sys

from pico.process import decode_output
from pico.runtime import Pico, SessionStore
from pico.providers.clients import FakeModelClient
from pico.workspace import WorkspaceContext


def test_utf8_output_is_preserved_with_legacy_windows_locale(monkeypatch):
    monkeypatch.setattr("pico.process.locale.getlocale", lambda category: ("zh_CN", "936"))
    assert decode_output("中文输出".encode("utf-8")) == "中文输出"
    assert decode_output("中文输出".encode("gbk")) == "中文输出"


def test_unknown_output_encoding_does_not_raise(monkeypatch):
    monkeypatch.setattr("pico.process.locale.getlocale", lambda category: (None, None))
    assert decode_output(b"\xfferror") == "\ufffderror"
    assert decode_output(None) == ""
    assert decode_output("already decoded") == "already decoded"


def test_shell_decodes_native_output_without_losing_exit_status(tmp_path, monkeypatch):
    monkeypatch.setattr("pico.process.locale.getlocale", lambda category: ("zh_CN", "936"))
    agent = Pico(
        FakeModelClient([]), WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / ".pico/sessions"), approval_policy="auto",
    )
    code = "import sys; sys.stdout.buffer.write(bytes.fromhex('d6d0cec4')); sys.exit(3)"
    argv = [sys.executable, "-c", code]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)

    result = agent.execute_tool("run_shell", {"command": command, "timeout": 20})

    assert "中文" in result.content
    assert "exit_code: 3" in result.content
    assert result.metadata["tool_status"] == "error"
