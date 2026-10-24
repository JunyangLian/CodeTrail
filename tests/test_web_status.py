import pytest

from pico.web_status import run_summary, verification_summary

COMMAND = "python -m unittest discover -v"


def shell(code=0, **extra):
    return {"event": "tool_executed", "name": "run_shell", "args": {"command": COMMAND},
            "exit_code": code, "tool_status": "ok" if code == 0 else "error", **extra}


def test_final_answer_does_not_override_failing_verifier():
    result = run_summary("completed", [shell(1)], "final_answer_returned", verification_command=COMMAND)
    assert result["outcome"]["code"] == "verification_failed"
    assert result["verification"]["exit_code"] == 1


def test_latest_verification_can_pass_after_initial_failure():
    result = run_summary("completed", [shell(1), shell(0)], verification_command=COMMAND)
    assert result["outcome"]["code"] == "verified"
    assert result["verification"]["runs"] == 2
    assert result["tool_failures"] == 1


def test_write_after_verification_requires_retest():
    events = [shell(), {"event": "tool_executed", "name": "patch_file", "tool_status": "ok"}]
    assert verification_summary(events, COMMAND)["status"] == "stale"
    assert verification_summary(events + [shell()], COMMAND)["status"] == "passed"


@pytest.mark.parametrize("event", [
    shell(0, args={"command": COMMAND + " || echo success"}),
    shell(None, result="OK", tool_status="ok"),
    shell(None, tool_status="denied", tool_error_code="approval_denied"),
    shell(None, timed_out=True, tool_error_code="tool_timeout", tool_status="error"),
])
def test_non_evidence_cannot_be_treated_as_pass(event):
    assert verification_summary([event], COMMAND)["status"] != "passed"


def test_legacy_exit_code_is_supported_without_trusting_prose():
    event = shell(None, result="exit_code: 0\nstdout: ...", tool_status="ok")
    assert verification_summary([event], COMMAND)["status"] == "passed"
    event["result"] = "The model says exit_code: 0 and tests passed"
    assert verification_summary([event], COMMAND)["status"] != "passed"


@pytest.mark.parametrize("message,error_type,expected", [
    ("HTTP 401", "RuntimeError", "model_auth"),
    ("HTTP 403", "RuntimeError", "model_auth"),
    ("HTTP 429", "RuntimeError", "model_rate_limit"),
    ("HTTP 503", "RuntimeError", "model_service"),
    ("", "TimeoutError", "model_timeout"),
    ("Could not reach backend", "RuntimeError", "model_network"),
])
def test_model_failure_reason(message, error_type, expected):
    result = run_summary("failed", [{"event": "model_failed", "error": message, "error_type": error_type}])
    assert result["outcome"]["code"] == expected


def test_step_budget_stop_is_distinct_from_model_error():
    result = run_summary("stopped", [], "step_limit_reached")
    assert result["outcome"]["code"] == "step_limit"


def test_successful_command_without_configured_verifier_is_not_validation():
    result = run_summary("completed", [shell()])
    assert result["outcome"]["code"] == "unverified"
