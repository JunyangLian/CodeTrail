"use strict";

const $ = (id) => document.getElementById(id);
const welcome = $("welcome").cloneNode(true);
const state = { config: null, sessionId: "", taskId: "", busy: false, messages: [], base: [], job: null, eventKey: "", diffKey: "" };
const statusNames = { queued: "准备中", running: "执行中", waiting_approval: "等待确认", finishing: "整理结果", completed: "已结束", failed: "执行异常", stopped: "已停止" };
let toastTimer;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function toast(message) {
  $("toast").textContent = message;
  $("toast").classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").classList.add("hidden"), 5000);
}
async function api(path, data) {
  const options = data === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Pico-Token": state.config.csrf },
    body: JSON.stringify(data),
  };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "请求失败，请刷新重试。");
  return result;
}
function setBusy(value) {
  state.busy = value;
  for (const id of ["send", "policy", "steps", "new-session"]) $(id).disabled = value;
  $("send").textContent = value ? "任务执行中…" : "开始任务 ↗";
  $("composer-state").textContent = value ? "可以在右侧查看实时过程" : policyHint();
}
function policyHint() {
  return { ask: "写入和命令执行会先征求确认", auto: "受控工具将自动执行", never: "写入和命令执行将被拒绝" }[$("policy").value];
}
function showMessages(messages) {
  const container = $("conversation");
  const stayAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 90;
  const fragment = document.createDocumentFragment();
  if (!messages.length) fragment.append(welcome.cloneNode(true));
  for (const message of messages) {
    const article = node("article", `message ${message.role}${message.pending ? " pending" : ""}${message.error ? " error" : ""}`);
    const label = node("div", "message-label");
    label.append(node("b", "", message.role === "user" ? "你" : "C"), node("span", "", message.role === "user" ? "你的任务" : "CodeTrail"));
    article.append(label, node("div", "message-body", message.content));
    fragment.append(article);
  }
  container.replaceChildren(fragment);
  if (stayAtBottom) container.scrollTop = container.scrollHeight;
}
function emptyView(symbol, title, description) {
  const empty = node("div", "detail-empty");
  empty.append(node("span", "", symbol), node("strong", "", title), node("p", "", description));
  return empty;
}
function resetInspector() {
  state.eventKey = "";
  state.diffKey = "";
  $("tool-count").textContent = "0";
  $("elapsed").textContent = "—";
  $("token-count").textContent = "—";
  $("diff-count").textContent = "0";
  $("task-status").className = "status-pill";
  $("task-status").textContent = "待开始";
  $("approval").classList.add("hidden");
  $("run-outcome").className = "run-outcome";
  $("outcome-title").textContent = "等待任务开始";
  $("outcome-detail").textContent = "模型回答结束与验证命令通过会分别展示。";
  $("verification-command").textContent = state.config?.verification_command || "";
  $("outcome-action").textContent = "";
  $("timeline").replaceChildren(emptyView("⌁", "每一步，都看得见", "开始任务后，这里显示真实执行过程。"));
  $("diffs").replaceChildren(emptyView("±", "等待代码变化", "任务结束后展示本次文本差异。"));
  $("tests").replaceChildren(emptyView("✓", "用执行结果说话", "运行命令的输出会显示在这里。"));
}
function selectTab(name) {
  if (!["timeline", "diffs", "tests"].includes(name)) return;
  for (const button of document.querySelectorAll("[data-tab]")) {
    const selected = button.dataset.tab === name;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    $(button.dataset.tab).classList.toggle("hidden", !selected);
  }
}
async function refreshSessions() {
  const sessions = await api("/api/sessions");
  const list = $("sessions");
  list.replaceChildren();
  for (const session of sessions) {
    const button = node("button", `session-item${state.sessionId === session.id ? " active" : ""}`);
    button.append(node("strong", "", session.title), node("small", "", formatDate(session.created_at)));
    button.title = session.id;
    button.addEventListener("click", () => selectSession(session.id));
    list.append(button);
  }
  if (!sessions.length) list.append(node("div", "muted small sidebar-empty", "你的第一个会话，会保存在这里。"));
}
function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "本地会话" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
async function selectSession(id) {
  if (state.busy) return toast("请等待当前任务结束后切换会话。");
  try {
    const data = await api(`/api/sessions/${encodeURIComponent(id)}`);
    state.sessionId = id;
    state.messages = data.messages;
    state.taskId = "";
    state.job = null;
    $("conversation-title").textContent = "继续历史会话";
    $("session-badge").textContent = id.slice(-13);
    showMessages(state.messages);
    $("conversation").scrollTop = $("conversation").scrollHeight;
    resetInspector();
    await refreshSessions();
  } catch (error) { toast(error.message); }
}
function newSession() {
  if (state.busy) return;
  state.sessionId = "";
  state.taskId = "";
  state.job = null;
  state.messages = [];
  state.base = [];
  $("conversation-title").textContent = "新会话";
  $("session-badge").textContent = "准备就绪";
  $("prompt").value = "";
  showMessages([]);
  resetInspector();
  refreshSessions().catch((error) => toast(error.message));
  $("prompt").focus();
}
async function send() {
  const prompt = $("prompt").value.trim();
  if (state.busy) return;
  if (!prompt) return toast("先描述一个你想完成的任务。");
  if (!state.config.configured) return toast("请先在本地 .env 配置模型 Key，并重启工作台。");
  const steps = Number($("steps").value);
  if (!Number.isInteger(steps) || steps < 1 || steps > 20) return toast("工具步数应在 1–20 之间。");
  state.base = [...state.messages, { role: "user", content: prompt }];
  resetInspector();
  setBusy(true);
  showMessages([...state.base, { role: "assistant", content: "正在准备工作区…", pending: true }]);
  try {
    const job = await api("/api/tasks", { prompt, session_id: state.sessionId, policy: $("policy").value, max_steps: steps });
    state.taskId = job.id;
    $("prompt").value = "";
    renderJob(job);
    poll(job.id);
  } catch (error) {
    setBusy(false);
    showMessages(state.messages);
    toast(error.message);
  }
}
async function poll(id) {
  if (state.taskId !== id) return;
  try {
    const job = await api(`/api/tasks/${id}`);
    if (state.taskId !== id) return;
    renderJob(job);
    if (["completed", "failed", "stopped"].includes(job.status)) {
      setBusy(false);
      await refreshSessions();
    } else setTimeout(() => poll(id), 1000);
  } catch (error) {
    $("connection").textContent = "连接中断 · 请刷新页面";
    toast(error.message);
  }
}
function renderJob(job) {
  state.job = job;
  if (job.session_id) {
    state.sessionId = job.session_id;
    $("session-badge").textContent = job.session_id.slice(-13);
    $("conversation-title").textContent = "当前会话";
  }
  const done = ["completed", "failed", "stopped"].includes(job.status);
  const messages = [...state.base];
  if (job.answer) messages.push({ role: "assistant", content: job.answer });
  if (job.error) messages.push({ role: "assistant", content: job.error, error: true });
  if (!done) messages.push({ role: "assistant", content: job.status === "waiting_approval" ? "等待你确认下一步操作。" : "CodeTrail 正在执行，右侧可以查看过程…", pending: true });
  state.messages = messages.filter((message) => !message.pending);
  showMessages(messages);
  $("task-status").className = `status-pill ${job.status}`;
  $("task-status").textContent = statusNames[job.status] || job.status;
  const outcome = job.outcome;
  if (outcome) {
    $("run-outcome").className = `run-outcome ${outcome.code}`;
    $("outcome-title").textContent = outcome.title;
    $("outcome-detail").textContent = outcome.detail;
    $("outcome-action").textContent = outcome.action;
    const verification = job.verification || {};
    const labels = { passed: "通过", failed: "失败", stale: "需重测", blocked: "未获准执行", not_run: "尚未执行" };
    $("verification-command").textContent = verification.command ? `${verification.command}\n${labels[verification.status] || "待核对"}${Number.isInteger(verification.exit_code) ? ` · 退出码 ${verification.exit_code}` : ""} · 本次执行 ${verification.runs || 0} 次` : "未指定验证命令";
  }
  $("tool-count").textContent = job.tool_steps || 0;
  $("elapsed").textContent = job.elapsed_ms ? formatDuration(job.elapsed_ms) : "—";
  const usage = job.usage && Object.keys(job.usage).length ? job.usage : (job.events || []).reduce((total, event) => {
    if (event.event === "model_parsed") {
      total.input_tokens += Number(event.completion_metadata?.input_tokens || 0);
      total.output_tokens += Number(event.completion_metadata?.output_tokens || 0);
    }
    return total;
  }, { input_tokens: 0, output_tokens: 0 });
  const tokens = usage.input_tokens + usage.output_tokens;
  $("token-count").textContent = tokens ? (tokens > 9999 ? `${(tokens / 1000).toFixed(1)}k` : tokens.toLocaleString()) : "—";
  $("token-count").title = `输入 ${usage.input_tokens || 0} / 输出 ${usage.output_tokens || 0}`;
  renderEvents(job.events || []);
  renderDiffs(job.diffs || [], done);
  renderApproval(job.approval);
}
function formatDuration(ms) {
  const seconds = Math.floor(ms / 1000);
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
const toolNames = { list_files: "列出文件", read_file: "读取文件", search: "搜索代码", patch_file: "修改代码", write_file: "写入文件", run_shell: "运行命令", delegate: "只读调查" };
function eventTitle(event) {
  const labels = { run_started: "开始任务", prompt_built: "准备上下文", model_requested: "请求模型", checkpoint_created: "保存恢复点", run_finished: "运行结束", model_failed: "模型请求失败", approval_requested: "等待你的确认", approval_resolved: event.allowed ? "操作已允许" : "操作已拒绝", runtime_identity_mismatch: "检查恢复现场", diff_unavailable: "差异快照未能保存" };
  if (event.event === "tool_executed") return toolNames[event.name] || event.name;
  if (event.event === "model_parsed") return event.kind === "tool" ? "收到工具调用" : event.kind === "final" ? "收到最终回复" : "重新整理模型回复";
  return labels[event.event] || null;
}
function renderEvents(events) {
  const key = JSON.stringify(events);
  if (state.eventKey === key) return;
  state.eventKey = key;
  const container = $("timeline");
  const follow = container.scrollHeight - container.scrollTop - container.clientHeight < 60;
  const opened = new Set([...container.querySelectorAll("details[open]")].map((item) => item.dataset.key));
  const fragment = document.createDocumentFragment();
  events.forEach((event, index) => {
    const title = eventTitle(event);
    if (!title) return;
    const bad = event.tool_status && event.tool_status !== "ok" || event.event === "model_failed";
    const row = node("div", `event${bad ? " error" : ""}`);
    const heading = node("div", "event-title");
    const date = new Date(event.created_at);
    heading.append(node("span", "", title), node("small", "", Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("zh-CN", { hour12: false })));
    row.append(node("span", "event-dot"), heading);
    let detail = event.args?.path || event.args?.command || event.tool_error_code || "";
    if (event.event === "prompt_built") detail = `${event.prompt_metadata?.prompt_chars || "—"} 字符上下文`;
    if (event.event === "checkpoint_created") detail = event.trigger || "";
    if (event.event === "run_finished") detail = `${statusNames[event.status] || event.status} · ${{ final_answer_returned: "模型返回最终回答", step_limit_reached: "工具步数已耗尽", retry_limit_reached: "回复格式多次无效", model_error: "模型请求失败" }[event.stop_reason] || event.stop_reason || ""}`;
    if (event.event === "model_failed") detail = event.error || "连接模型时发生错误";
    if (detail) row.append(node("div", "event-detail", detail));
    if (bad && event.event === "tool_executed") {
      const reasons = { approval_denied: "操作未获批准", tool_not_allowed: "当前模式禁止此工具", invalid_arguments: "工具参数无效", unknown_tool: "工具不存在", repeated_identical_call: "重复调用被拦截", tool_timeout: "命令执行超时", tool_failed: "工具执行失败", tool_partial_success: "发生修改，但命令未成功结束" };
      row.append(node("div", "event-detail", reasons[event.tool_error_code] || "工具执行异常"));
    }
    if (event.result || event.event === "approval_requested") {
      const details = node("details");
      details.dataset.key = String(index);
      details.open = opened.has(String(index));
      details.append(node("summary", "", "查看记录"), node("pre", "", event.result || JSON.stringify(event.args, null, 2)));
      row.append(details);
    }
    fragment.append(row);
  });
  if (!fragment.childNodes.length) fragment.append(emptyView("⌁", "正在准备", "执行事件即将出现。"));
  container.replaceChildren(fragment);
  if (follow) container.scrollTop = container.scrollHeight;
  const tests = events.filter((event) => event.event === "tool_executed" && event.name === "run_shell");
  if (!tests.length) $("tests").replaceChildren(emptyView("✓", "还没有运行命令", "测试和其他命令的真实输出会保留在这里。"));
  else {
    const cards = tests.map((event, index) => {
      const card = node("div", "test-card");
      const code = Number.isInteger(event.exit_code) ? event.exit_code : event.result?.match(/^\s*exit_code:\s*(-?\d+)/)?.[1];
      const label = event.timed_out ? "执行超时" : code !== undefined ? `退出码 ${code}` : "无退出码 · 未执行或异常";
      card.append(node("h3", "", `命令 ${index + 1}`), node("code", "", event.args?.command || ""), node("span", `test-status${event.tool_status === "ok" ? "" : " error"}`, label), node("pre", "test-output", event.result || "没有记录输出"));
      return card;
    });
    $("tests").replaceChildren(...cards);
  }
}
function renderDiffs(diffs, done) {
  const key = JSON.stringify([diffs, done]);
  if (state.diffKey === key) return;
  state.diffKey = key;
  $("diff-count").textContent = diffs.length;
  if (!diffs.length) return $("diffs").replaceChildren(emptyView("±", done ? "没有文本差异" : "等待任务结束", done ? "本次没有可展示的文本变更，或历史记录未保存差异快照。" : "任务结束后生成代码差异。"));
  $("diffs").replaceChildren(...diffs.map((file) => {
    const card = node("div", "diff-file");
    const pre = node("pre", "diff-content");
    for (const line of file.diff.split("\n")) {
      const type = line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "remove" : line.startsWith("@@") ? "hunk" : "";
      pre.append(node("span", `diff-line ${type}`, line));
    }
    card.append(node("strong", "", file.path), pre);
    if (file.truncated) card.append(node("small", "muted", "差异过长，仅展示前 50000 字符。"));
    return card;
  }));
}
function renderApproval(approval) {
  $("approval").classList.toggle("hidden", !approval);
  if (!approval) return;
  $("approval-name").textContent = toolNames[approval.name] || approval.name;
  $("approval-args").textContent = JSON.stringify(approval.args, null, 2);
  $("approve").disabled = false;
  $("deny").disabled = false;
}
async function decide(allowed) {
  const approval = state.job?.approval;
  if (!approval) return;
  $("approve").disabled = true;
  $("deny").disabled = true;
  try {
    await api(`/api/tasks/${state.taskId}/approvals/${approval.id}`, { allowed });
    $("approval").classList.add("hidden");
  } catch (error) { toast(error.message); }
}
async function history() {
  if (state.busy) return toast("当前任务执行中，结束后可查看其他运行。");
  try {
    const runs = await api("/api/runs");
    const list = $("run-history");
    list.replaceChildren();
    for (const run of runs) {
      const button = node("button", "run-row");
      const description = node("span");
      description.append(node("strong", "", run.title), node("small", "", run.id));
      button.append(description, node("b", "", run.outcome?.title || statusNames[run.status] || run.status));
      button.addEventListener("click", async () => {
        try {
          const job = await api(`/api/runs/${run.id}`);
          state.taskId = "";
          state.sessionId = job.session_id || "";
          state.base = job.prompt ? [{ role: "user", content: job.prompt }] : [];
          renderJob(job);
          $("conversation-title").textContent = "历史运行";
          $("session-badge").textContent = run.id.slice(-13);
          $("history-dialog").close();
          await refreshSessions();
        } catch (error) { toast(error.message); }
      });
      list.append(button);
    }
    if (!runs.length) list.append(node("p", "muted", "当前工作区还没有运行记录。"));
    $("history-dialog").showModal();
  } catch (error) { toast(error.message); }
}

$("send").addEventListener("click", send);
$("new-session").addEventListener("click", newSession);
$("refresh-sessions").addEventListener("click", () => refreshSessions().catch((error) => toast(error.message)));
$("show-history").addEventListener("click", history);
$("close-history").addEventListener("click", () => $("history-dialog").close());
$("approve").addEventListener("click", () => decide(true));
$("deny").addEventListener("click", () => decide(false));
$("policy").addEventListener("change", () => $("composer-state").textContent = policyHint());
document.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-tab]");
  if (tab) selectTab(tab.dataset.tab);
  const suggestion = event.target.closest("[data-prompt]");
  if (suggestion && !state.busy) { $("prompt").value = suggestion.dataset.prompt; $("prompt").focus(); }
});
document.addEventListener("keydown", (event) => {
  const tab = event.target.closest("[data-tab]");
  if (tab && ["ArrowRight", "ArrowLeft"].includes(event.key)) {
    event.preventDefault();
    const tabs = [...document.querySelectorAll("[data-tab]")];
    const next = tabs[(tabs.indexOf(tab) + (event.key === "ArrowRight" ? 1 : 2)) % tabs.length];
    selectTab(next.dataset.tab);
    next.focus();
  }
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && document.activeElement === $("prompt")) { event.preventDefault(); send(); }
  if (event.key.toLowerCase() === "n" && !event.ctrlKey && !event.metaKey && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) newSession();
});
async function init() {
  try {
    state.config = await api("/api/config");
    const command = state.config.verification_command;
    if (state.config.demo) $("steps").value = "12";
    if (command) {
      for (const root of [welcome, document]) {
        root.querySelector('[data-suggestion="verify"]').dataset.prompt = `请只运行 ${command}，汇报实际退出码和结果，不修改任何文件。`;
        if (state.config.demo) {
          root.querySelector('[data-suggestion="inspect"]').dataset.prompt = "请读取 README.md、order_pricing.py 和 test_order_pricing.py，说明订单计价规则及函数关系，不修改文件。";
          root.querySelector('[data-suggestion="repair"]').dataset.prompt = `先运行 ${command} 查看真实失败，再读取 order_pricing.py，只修复这个实现文件。业务规则：商品金额为单价乘数量；折扣10表示九折；折后满100免运费否则8，空订单0；保留输入校验和Decimal精度。最后运行同一测试命令，汇报实际结果。不要修改测试、calculator.py 或其他文件，不运行重置。若测试已通过则说明结果，不制造错误。`;
        }
      }
    }
    $("verification-command").textContent = command || "";
    $("workspace-name").textContent = state.config.workspace_name;
    $("workspace-path").textContent = state.config.workspace;
    $("workspace-path").title = state.config.workspace;
    $("model-name").textContent = state.config.model;
    const executionMode = state.config.shell_backend === "appcontainer" ? "AppContainer 隔离模式" : "宿主执行 · 无 OS 隔离";
    $("connection").replaceChildren(node("span", "local-dot"), document.createTextNode(state.config.configured ? `模型已配置 · ${executionMode}` : `模型尚未配置 · ${executionMode}`));
    await refreshSessions();
    if (state.config.active_id) {
      const job = await api(`/api/tasks/${state.config.active_id}`);
      state.taskId = job.id;
      state.base = [{ role: "user", content: job.prompt }];
      setBusy(true);
      renderJob(job);
      poll(job.id);
    }
  } catch (error) {
    $("connection").textContent = "无法连接本地服务";
    $("send").disabled = true;
    toast(error.message);
  }
}
init();
