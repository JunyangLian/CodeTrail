import sys

import pytest

from pico.evaluation.evaluator import _normalize_verifier, _run_verifier, _verifier_invocation


@pytest.mark.parametrize("interpreter", ["python", "python3", "python.exe", "python3.exe"])
def test_legacy_python_verifier_runs_in_active_environment(tmp_path, interpreter):
    (tmp_path / "结果.txt").write_text("通过", encoding="utf-8")
    code = 'import pathlib; assert pathlib.Path("结果.txt").read_text(encoding="utf-8") == "通过"'

    result = _run_verifier(f"{interpreter} -c '{code}'", tmp_path)

    assert result.returncode == 0, result.stderr


def test_argv_verifier_preserves_spaces_quotes_and_shell_metacharacters(tmp_path):
    code = "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')"
    payload = '中文 \'single\' "double" & | > ; $HOME'
    command = ["python3", "-c", code, "file with spaces.txt", payload]

    result = _run_verifier(command, tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "file with spaces.txt").read_text(encoding="utf-8") == payload
    assert command[0] == "python3"


def test_verifier_captures_unicode_output_and_failure_exit_code(tmp_path):
    result = _run_verifier(
        ["python", "-c", "import sys; print('验证失败'); print('错误详情', file=sys.stderr); sys.exit(3)"],
        tmp_path,
    )

    assert result.returncode == 3
    assert result.stdout.strip() == "验证失败"
    assert result.stderr.strip() == "错误详情"


def test_legacy_double_quotes_preserve_python_string_literals(tmp_path):
    result = _run_verifier('python3 -c "assert \'quoted\' == \'quoted\'"', tmp_path)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("command", [
    "echo first && echo second",
    "python -c 'print(1)' | echo second",
    "python -c 'print(1)' > result.txt",
    "python -c 'print(1)'&&echo second",
])
def test_legacy_compound_commands_keep_shell_semantics(command):
    assert _verifier_invocation(command) == (command, True)


def test_explicit_interpreter_path_is_preserved():
    command = [sys.executable, "-c", "print(1)"]

    assert _verifier_invocation(command) == (command, False)


@pytest.mark.parametrize("command", ["", "  ", [], [""], ["python", 1], None, {}])
def test_verifier_rejects_invalid_command_shapes(command):
    with pytest.raises(ValueError, match="verifier"):
        _normalize_verifier(command)
