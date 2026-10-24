"""Evidence-based run summaries for the local workbench."""

import re


def shell_exit_code(event):
    value = event.get("exit_code")
    if type(value) is int:
        return value
    # Older traces have the exit code only in the output preview.
    match = re.search(r"^\s*exit_code:\s*(-?\d+)", event.get("result", ""))
    return int(match.group(1)) if match else None


def verification_summary(events, command):
    result = {"status": "not_run" if command else "not_configured",
              "command": command or "", "exit_code": None, "runs": 0}
    if not command:
        return result
    for event in events:
        if event.get("event") != "tool_executed":
            continue
        matches = (event.get("name") == "run_shell"
                   and str(event.get("args", {}).get("command", "")).strip() == command.strip())
        if matches:
            code = shell_exit_code(event)
            result.update(exit_code=code, runs=result["runs"] + 1)
            if event.get("tool_error_code") in {"approval_denied", "tool_not_allowed"}:
                result["status"] = "blocked"
            elif code == 0 and event.get("tool_status") == "ok":
                result["status"] = "passed"
            else:
                result["status"] = "failed"
        elif result["runs"] and (event.get("workspace_changed") or (
                event.get("name") in {"write_file", "patch_file"} and event.get("tool_status") == "ok")):
            result["status"] = "stale"
    return result


def model_failure(error, error_type=""):
    text = str(error).lower()
    if re.search(r"\b(401|403)\b", text):
        return ("model_auth", "模型鉴权失败", "检查本地 Key、模型访问权限和服务地址，保存后重启工作台。")
    if re.search(r"\b429\b", text):
        return ("model_rate_limit", "模型接口限流或额度不足", "查看服务控制台的限流、余额和配额，再决定何时重试。")
    if "timeout" in text or "timed out" in text or error_type == "TimeoutError":
        return ("model_timeout", "模型请求超时", "检查网络与服务状态；必要时调整超时配置后再重试。")
    if re.search(r"\b5\d\d\b", text):
        return ("model_service", "模型服务异常", "查看服务状态，稍后重新提交任务。")
    if any(value in text for value in ("could not reach", "network", "connection", "ssl", "urlopen", "resolve")):
        return ("model_network", "无法连接模型服务", "检查服务地址、网络和代理设置，再重试。")
    return ("model_error", "模型请求失败", "展开模型失败记录，核对模型名称、接口格式和服务配置。")


def run_summary(status, events, stop_reason="", error="", verification_command=None):
    finished = next((e for e in reversed(events) if e.get("event") == "run_finished"), {})
    reason = stop_reason or finished.get("stop_reason", "")
    verification = verification_summary(events, verification_command)
    failures = [e for e in events if e.get("event") == "tool_executed"
                and e.get("tool_status") not in {None, "ok"}]
    failed_model = next((e for e in reversed(events) if e.get("event") == "model_failed"), None)
    detail = ""
    action = ""
    if status in {"queued", "running", "waiting_approval", "finishing"}:
        code, title = "running", "任务尚未结束"
        detail = "结果会随真实工具记录更新。"
    elif reason == "model_error" or failed_model:
        source = failed_model or {}
        detail = source.get("error") or error
        code, title, action = model_failure(detail, source.get("error_type", ""))
    elif reason == "step_limit_reached":
        code, title = "step_limit", "工具步数已耗尽"
        detail = "本次运行未获得有效的最终回答。"
        action = "查看最后一步；缩小任务或增加步数后继续会话。"
    elif reason == 'verification_incomplete':
        code, title = 'verification_incomplete', '验证条件未满足，任务未完成'
        detail = '指定验证命令尚未在最后一次修改后通过，或执行审批被拒绝。'
        action = '查看测试输出和审批记录，再决定是否继续修复。'
    elif reason == "retry_limit_reached":
        code, title = "response_format", "模型回复格式多次无效"
        action = "查看模型回复记录，核对模型与工具调用协议。"
    elif status == "failed":
        code, title, detail = "runtime_error", "执行异常", error or "执行未能正常结束。"
        action = "查看过程中的错误记录，处理原因后继续会话。"
    elif status == "stopped":
        code, title, detail = "stopped", "运行已停止", reason or "请查看最后一条执行记录。"
    elif verification["status"] == "passed":
        code, title = "verified", "验证命令通过"
        detail = "指定验证命令的退出码为 0；这不替代对测试覆盖范围和代码差异的检查。"
    elif verification["status"] == "failed":
        code, title = "verification_failed", "模型已结束 · 验证未通过"
        detail = "指定验证命令失败，模型的完成说明不能替代执行结果。"
        action = "查看验证输出并继续修复。"
    elif verification["status"] == "stale":
        code, title = "verification_stale", "模型已结束 · 需要重新验证"
        detail = "最近一次验证后工作区又发生变化。"
        action = "重新运行指定验证命令。"
    elif verification["status"] == "blocked":
        code, title = "verification_blocked", "模型已结束 · 验证被阻止"
        detail = "验证命令没有获得执行权限。"
        action = "核对命令后允许执行，或在终端独立验证。"
    else:
        code, title = "unverified", "模型已结束 · 未验证"
        detail = "尚未执行指定验证命令。" if verification_command else "未指定验证命令；请核对实际命令输出。"
    if failures and status == "completed":
        detail += f" 执行中出现 {len(failures)} 次工具异常或拦截，详见过程记录。"
    return {"stop_reason": reason, "verification": verification, "tool_failures": len(failures),
            "outcome": {"code": code, "title": title, "detail": detail, "action": action}}
