"""Loopback-only workbench for the existing Pico runtime. No web dependencies."""

import argparse
import difflib
import hmac
import json
import re
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .cli import _build_model_client, _configured_secret_names, build_arg_parser
from .config import load_project_env
from .runtime import Pico
from .security import redact_artifact
from .session_store import SessionStore
from .workspace import IGNORED_PATH_NAMES, WorkspaceContext, now
from .web_status import run_summary

ASSETS = Path(__file__).parent / "web_assets"
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")


class RequestError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def checked_id(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise RequestError("无效的记录 ID。")
    return value


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def text_snapshot(root):
    """Bounded text snapshot, excluding internal state, secrets and symlinks."""
    result = {}
    total = 0
    ignored = IGNORED_PATH_NAMES | {"node_modules", "dist", "build"}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in ignored or part.startswith(".env") for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        try:
            if path.stat().st_size > 200_000:
                continue
            data = path.read_bytes()
            if b"\0" in data:
                continue
            result[relative.as_posix()] = data.decode("utf-8")
            total += len(data)
            if total >= 2_000_000 or len(result) >= 400:
                break
        except (OSError, UnicodeDecodeError):
            continue
    return result


def make_diffs(before, after):
    diffs = []
    for path in sorted(before.keys() | after.keys()):
        if before.get(path) == after.get(path):
            continue
        lines = list(difflib.unified_diff(
            before.get(path, "").splitlines(), after.get(path, "").splitlines(),
            fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
        ))
        content = "\n".join(lines)
        diffs.append({"path": path, "diff": content[:50_000], "truncated": len(content) > 50_000})
        if len(diffs) == 20:
            break
    return diffs


class Task:
    def __init__(self, prompt, session_id, policy, max_steps, verification_command=None):
        self.id = secrets.token_hex(12)
        self.prompt = prompt
        self.session_id = session_id or ""
        self.policy = policy
        self.max_steps = max_steps
        self.status = "queued"
        self.created_at = now()
        self.started = time.monotonic()
        self.finished = None
        self.run_id = ""
        self.answer = ""
        self.error = ""
        self.stop_reason = ""
        self.verification_command = verification_command
        self.events = []
        self.diffs = []
        self.approval = None
        self.decision = None
        self.attempts = 0
        self.tool_steps = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.condition = threading.Condition()

    def view(self):
        with self.condition:
            return redact_artifact({
                "id": self.id, "prompt": self.prompt, "session_id": self.session_id,
                "run_id": self.run_id, "status": self.status, "answer": self.answer,
                "error": self.error, "events": list(self.events), "diffs": list(self.diffs),
                "approval": dict(self.approval) if self.approval else None,
                "attempts": self.attempts, "tool_steps": self.tool_steps,
                "usage": dict(self.usage), "created_at": self.created_at,
                "elapsed_ms": int(((self.finished or time.monotonic()) - self.started) * 1000),
                **run_summary(self.status, self.events, self.stop_reason, self.error, self.verification_command),
            })


class Workbench:
    def __init__(self, workspace, factory=None, verification_command=None, model_timeout=60):
        if type(model_timeout) is not int or not 1 <= model_timeout <= 300:
            raise ValueError("模型超时应在 1–300 秒之间。")
        self.root = Path(workspace).resolve()
        self.verification_command = verification_command
        if verification_command is None and self.root == (Path(__file__).resolve().parent.parent / "examples/interview-demo"):
            self.verification_command = "python -m unittest discover -v"
        if not self.root.is_dir():
            raise ValueError("工作目录不存在。")
        load_project_env(self.root)
        self.args = build_arg_parser().parse_args([
            "--cwd", str(self.root), "--openai-timeout", str(model_timeout),
            "--ollama-timeout", str(model_timeout),
        ])
        self.model = _build_model_client(self.args)
        self.factory = factory or self._build_agent
        self.csrf = secrets.token_urlsafe(32)
        self.tasks = {}
        self.active_id = None
        self.lock = threading.Lock()

    def config(self):
        return {
            "workspace": str(self.root), "workspace_name": self.root.name,
            "model": self.model.model, "configured": bool(getattr(self.model, "api_key", True)),
            "model_timeout": self.args.openai_timeout,
            "csrf": self.csrf, "active_id": self.active_id,
            "verification_command": self.verification_command,
            "demo": self.root == (Path(__file__).resolve().parent.parent / "examples/interview-demo"),
        }

    def _build_agent(self, session_id, policy, max_steps):
        model = _build_model_client(self.args)
        if hasattr(model, "api_key") and not model.api_key:
            raise RequestError("请先在本地 .env 填入模型 API Key，然后重启工作台。")
        workspace = WorkspaceContext.build(self.root, repo_root_override=self.root)
        store = SessionStore(self.root / ".pico/sessions")
        options = dict(
            model_client=model, workspace=workspace, session_store=store,
            approval_policy=policy, read_only=policy == "never", max_steps=max_steps,
            max_new_tokens=1024, secret_env_names=_configured_secret_names(self.args),
        )
        if session_id:
            return Pico.from_session(session_id=session_id, **options)
        return Pico(**options)

    def submit(self, data):
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16_000:
            raise RequestError("请输入任务，长度不超过 16000 字符。")
        policy = data.get("policy", "ask")
        if policy not in {"ask", "auto", "never"}:
            raise RequestError("无效的执行模式。")
        steps = data.get("max_steps", 8)
        if type(steps) is not int or not 1 <= steps <= 20:
            raise RequestError("工具步数应在 1–20 之间。")
        session_id = data.get("session_id") or ""
        if session_id:
            self.session(session_id)
        with self.lock:
            if self.active_id:
                raise RequestError("已有任务正在执行，请等待完成。", 409)
            task = Task(prompt.strip(), session_id, policy, steps, self.verification_command)
            self.tasks[task.id] = task
            self.active_id = task.id
        threading.Thread(target=self._execute, args=(task,), daemon=True).start()
        return task.view()

    def task(self, task_id):
        task = self.tasks.get(checked_id(task_id))
        if not task:
            raise RequestError("任务不存在或服务已重启，请查看运行记录。", 404)
        return task

    def approve(self, task_id, approval_id, allowed):
        task = self.task(task_id)
        if type(allowed) is not bool:
            raise RequestError("审批结果必须是布尔值。")
        with task.condition:
            if not task.approval or task.approval["id"] != approval_id or task.decision is not None:
                raise RequestError("这项审批已结束或不存在。", 409)
            task.decision = allowed
            task.condition.notify_all()
        return {"ok": True}

    def _execute(self, task):
        before = {}
        agent = None
        terminal_status = "failed"
        try:
            agent = self.factory(task.session_id, task.policy, task.max_steps)
            original_emit = agent.emit_trace

            def emit(state, event, payload=None):
                saved = original_emit(state, event, payload)
                with task.condition:
                    task.events.append(saved)
                    task.run_id = state.run_id
                    task.attempts = state.attempts
                    task.tool_steps = state.tool_steps
                    if event == "model_parsed":
                        usage = saved.get("completion_metadata", {})
                        for key in task.usage:
                            task.usage[key] += int(usage.get(key) or 0)
                    task.condition.notify_all()
                return saved

            def request_approval(name, args):
                if task.policy == "never":
                    return False
                if task.policy == "auto":
                    return True
                with task.condition:
                    task.approval = {"id": secrets.token_hex(8), "name": name, "args": agent.redact_artifact(args)}
                    task.decision = None
                    task.status = "waiting_approval"
                agent.emit_trace(agent.current_task_state, "approval_requested", task.approval)
                with task.condition:
                    task.condition.wait_for(lambda: task.decision is not None, timeout=600)
                    allowed = task.decision is True
                    task.approval = None
                    task.status = "running"
                agent.emit_trace(agent.current_task_state, "approval_resolved", {"name": name, "allowed": allowed})
                return allowed

            agent.emit_trace = emit
            agent.approve = request_approval
            before = text_snapshot(self.root)
            with task.condition:
                task.session_id = agent.session["id"]
                task.status = "running"
            answer = agent.ask(task.prompt)
            with task.condition:
                task.answer = agent.redact_text(answer)
                terminal_status = agent.current_task_state.status
                task.stop_reason = agent.current_task_state.stop_reason
                task.status = "finishing"
        except Exception as exc:
            with task.condition:
                task.error = redact_artifact(str(exc))
                task.stop_reason = getattr(getattr(agent, "current_task_state", None), "stop_reason", "")
                task.status = "finishing"
        finally:
            if agent:
                try:
                    diffs = redact_artifact(make_diffs(before, text_snapshot(self.root)))
                    with task.condition:
                        task.diffs = diffs
                    if task.run_id:
                        directory = self.root / ".pico/web"
                        directory.mkdir(parents=True, exist_ok=True)
                        agent.run_store._write_json_atomic(directory / f"{task.run_id}.json", {
                            "session_id": task.session_id, "diffs": diffs,
                            "verification_command": task.verification_command, "error": task.error,
                        })
                except Exception:
                    with task.condition:
                        task.events.append({"event": "diff_unavailable", "created_at": now()})
            with self.lock:
                self.active_id = None
            with task.condition:
                task.status = terminal_status
                task.finished = time.monotonic()
                task.approval = None
                task.condition.notify_all()

    def session(self, session_id):
        session_id = checked_id(session_id)
        data = read_json(self.root / ".pico/sessions" / f"{session_id}.json")
        if not data:
            raise RequestError("会话不存在或正在保存，请稍后刷新。", 404)
        messages = [{"role": item["role"], "content": item.get("content", "")}
                    for item in data.get("history", []) if item.get("role") in {"user", "assistant"}]
        return redact_artifact({"session_id": session_id, "messages": messages[-40:]})

    def sessions(self):
        rows = []
        paths = sorted((self.root / ".pico/sessions").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in paths[:30]:
            data = read_json(path, {})
            history = data.get("history", [])
            title = next((str(m.get("content", ""))[:65] for m in history if m.get("role") == "user"), "空白会话")
            rows.append({"id": path.stem, "title": title, "created_at": data.get("created_at", "")})
        return redact_artifact(rows)

    def runs(self):
        rows = []
        for path in sorted((self.root / ".pico/runs").glob("*/report.json"), reverse=True)[:30]:
            report = read_json(path, {})
            if report:
                meta = read_json(self.root / ".pico/web" / f"{path.parent.name}.json", {})
                summary = run_summary(report.get("status", "failed"), self._run_events(path.parent),
                                      report.get("stop_reason", ""), meta.get("error", ""), meta.get("verification_command"))
                rows.append({"id": path.parent.name, "status": report.get("status"),
                             "outcome": summary["outcome"],
                             "title": str(report.get("task_state", {}).get("user_request", "运行记录"))[:90]})
        return redact_artifact(rows)

    @staticmethod
    def _run_events(directory):
        events = []
        try:
            for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            pass
        return events

    def run(self, run_id):
        directory = self.root / ".pico/runs" / checked_id(run_id)
        report = read_json(directory / "report.json")
        if report is None:
            raise RequestError("运行记录不存在。", 404)
        events = self._run_events(directory)
        meta = read_json(self.root / ".pico/web" / f"{run_id}.json", {})
        return redact_artifact({
            "id": "", "run_id": run_id, "session_id": meta.get("session_id", ""),
            "status": report.get("status", "failed"), "events": events[-500:],
            "diffs": meta.get("diffs", []), "answer": report.get("final_answer", ""),
            "error": meta.get("error", ""), "approval": None, "usage": {},
            **run_summary(report.get("status", "failed"), events, report.get("stop_reason", ""),
                          meta.get("error", ""), meta.get("verification_command")),
            "elapsed_ms": next((e.get("run_duration_ms", 0) for e in reversed(events) if e.get("event") == "run_finished"), 0),
            "prompt": report.get("task_state", {}).get("user_request", ""),
            "attempts": report.get("attempts", 0), "tool_steps": report.get("tool_steps", 0),
        })


def make_server(workbench, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, payload, content_type="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def validate_host(self):
            port = self.server.server_port
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                raise RequestError("只接受本地工作台请求。", 403)

        def do_GET(self):
            try:
                self.validate_host()
                path = urlsplit(self.path).path
                if path in {"/", "/app.js", "/style.css"}:
                    name = "index.html" if path == "/" else path[1:]
                    kind = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[name]
                    return self.reply(200, (ASSETS / name).read_bytes(), kind + "; charset=utf-8")
                if path == "/api/config":
                    return self.reply(200, workbench.config())
                if path == "/api/sessions":
                    return self.reply(200, workbench.sessions())
                if path.startswith("/api/sessions/"):
                    return self.reply(200, workbench.session(path.split("/")[-1]))
                if path == "/api/runs":
                    return self.reply(200, workbench.runs())
                if path.startswith("/api/runs/"):
                    return self.reply(200, workbench.run(path.split("/")[-1]))
                if path.startswith("/api/tasks/"):
                    return self.reply(200, workbench.task(path.split("/")[-1]).view())
                raise RequestError("页面不存在。", 404)
            except RequestError as exc:
                self.reply(exc.status, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            try:
                self.validate_host()
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    raise RequestError("请求来源不匹配。", 403)
                if not hmac.compare_digest(self.headers.get("X-Pico-Token", "").encode(), workbench.csrf.encode()):
                    raise RequestError("请刷新工作台后重试。", 403)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65_536:
                    raise RequestError("请求过大或为空。", 413)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise RequestError("请求必须是 JSON 对象。")
                path = urlsplit(self.path).path
                if path == "/api/tasks":
                    return self.reply(202, workbench.submit(data))
                parts = path.strip("/").split("/")
                if len(parts) == 5 and parts[:2] == ["api", "tasks"] and parts[3] == "approvals":
                    return self.reply(200, workbench.approve(parts[2], parts[4], data.get("allowed")))
                raise RequestError("接口不存在。", 404)
            except RequestError as exc:
                self.reply(exc.status, {"error": str(exc)})
            except (ValueError, UnicodeDecodeError):
                self.reply(400, {"error": "请求格式不正确。"})
            except (BrokenPipeError, ConnectionResetError):
                pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="CodeTrail local coding workbench")
    parser.add_argument("--cwd", default=".", help="Fixed workspace for this workbench")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the local page in your browser")
    parser.add_argument("--verify-command", help="Exact shell command used as verification evidence; never runs automatically")
    parser.add_argument("--model-timeout", type=int, default=60, help="Model request timeout in seconds (1-300)")
    args = parser.parse_args(argv)
    if not 1 <= args.model_timeout <= 300:
        parser.error("--model-timeout must be between 1 and 300 seconds")
    app = Workbench(args.cwd, verification_command=args.verify_command, model_timeout=args.model_timeout)
    server = make_server(app, args.port)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"CodeTrail workbench: {url}\nWorkspace: {app.root}\nPress Ctrl+C to stop.", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
