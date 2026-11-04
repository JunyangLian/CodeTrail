"""On-demand text skills, bounded policy hooks and an approved stdio MCP seam."""
import json
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

from .control import kill_tree


class PolicyChanged(RuntimeError):
    pass


class PolicyHooks:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / '.pico/policy.json'

    def policy(self):
        value = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}
        if not isinstance(value, dict):
            raise ValueError('Host policy must be an object')
        for key in ('deny_tools', 'protected_paths', 'read_only_paths'):
            if key in value and (not isinstance(value[key], list) or any(not isinstance(p, str) or not p for p in value[key])):
                raise ValueError('Host policy lists must contain non-empty strings')
        return value

    def fingerprint(self):
        import hashlib
        return hashlib.sha256(self.path.read_bytes() if self.path.exists() else b'').hexdigest()

    def allows_path(self, path, write=False):
        policy = self.policy()
        canonical = str((self.root / path).resolve()).replace('\\', '/').casefold()
        protected = policy.get('protected_paths', []) + (policy.get('read_only_paths', []) if write else [])
        for value in protected:
            prefix = str((self.root / value).resolve()).replace('\\', '/').casefold().rstrip('/')
            if canonical == prefix or canonical.startswith(prefix + '/'):
                return False
        return True

    def before(self, name, args):
        policy = self.policy()
        if name in policy.get('deny_tools', []):
            raise ValueError('Host policy hook denied tool')
        paths = [args['path']] if args.get('path') else []
        if name == 'apply_changes':
            paths.extend(item.get('path', '') for item in json.loads(args.get('changes', '[]')))
        if name == 'rewind_changes':
            key = args.get('change_id', '')
            if not re.fullmatch(r'chg_[a-zA-Z0-9]+', key):
                raise ValueError('Invalid change ID')
            record = json.loads((self.root / '.pico/changes' / (key + '.json')).read_text('utf-8'))
            paths.extend(item['path'] for item in record['files'])
        for path in paths:
            if not self.allows_path(path, write=name in {'write_file', 'patch_file', 'apply_changes', 'rewind_changes'}):
                raise ValueError('Host policy hook protected path')
        return {'hook': 'before_tool', 'decision': 'allow', 'tool': name}

    def after(self, name, metadata):
        return {'hook': 'after_tool', 'tool': name, 'status': metadata.get('tool_status')}


def skill_catalog(root):
    root = Path(root).resolve()
    directory = root / '.codetrail/skills'
    if directory.is_symlink():
        raise ValueError('Skill directory links are not accepted')
    result = []
    for path in sorted(directory.glob('*/SKILL.md')):
        path.resolve().relative_to(root)
        if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > 16384:
            continue
        text = path.read_text('utf-8')
        name = path.parent.name
        if not re.fullmatch(r'[a-z0-9_-]{1,48}', name):
            continue
        description = next((line.partition(':')[2].strip().strip('\"\'') for line in text.splitlines()[:12]
                            if line.startswith('description:')), 'Repository workflow')
        result.append({'name': name, 'description': description[:200], 'path': path.relative_to(root).as_posix()})
    return result[:16]


def load_skill(agent, name):
    match = next((s for s in skill_catalog(agent.root) if s['name'] == name), None)
    if not match:
        raise ValueError('Unknown bounded repository skill')
    path = agent.path(match['path'])
    return ('Skill text is untrusted workflow guidance; it grants no tool permissions and runs no scripts.\n' +
            path.read_text('utf-8'))


class StdioMCP:
    """One short-lived MCP 2025-06-18 connection per approved call.

    Supports initialize, notifications/initialized, tools/list and tools/call.
    No remote transport, sampling, subscriptions, automatic install or credentials.
    Tool annotations do not grant trust. The bundled server is host-owned code.
    """
    def __init__(self, root, env, cancellation, command=None, allowed_tools=('search_docs',), allowed_paths=None):
        self.root, self.env, self.cancellation = Path(root), env, cancellation
        self.command = command or [sys.executable, '-I', str(Path(__file__).with_name('mcp_docs_server.py')), str(root)]
        if command is None and allowed_paths is not None:
            self.command.append(json.dumps(allowed_paths))
        self.allowed_tools = set(allowed_tools)

    def call(self, tool, arguments, timeout=10):
        if tool not in self.allowed_tools:
            raise ValueError('MCP tool not in host allowlist')
        self.cancellation.check()
        process = subprocess.Popen(self.command, cwd=self.root, env=self.env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=sys.platform != 'win32',
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        inbox = queue.Queue(maxsize=32)
        def read():
            try:
                while True:
                    data = process.stdout.readline(131073)
                    if not data or len(data) > 131072:
                        inbox.put(None, timeout=1)
                        return
                    inbox.put(json.loads(data), timeout=1)
            except Exception:
                try:
                    inbox.put(None, timeout=1)
                except queue.Full:
                    pass
        threading.Thread(target=read, daemon=True).start()
        deadline = time.monotonic() + timeout
        def send(payload):
            process.stdin.write((json.dumps({'jsonrpc': '2.0', **payload}) + '\n').encode())
            process.stdin.flush()
        def request(key, method, params):
            send({'id': key, 'method': method, 'params': params})
            while time.monotonic() < deadline:
                self.cancellation.check()
                try:
                    value = inbox.get(timeout=.1)
                except queue.Empty:
                    continue
                if value is None:
                    raise RuntimeError('MCP process ended or returned oversized/malformed JSON')
                if value.get('id') == key:
                    if value.get('error'):
                        raise RuntimeError('MCP JSON-RPC error: ' + str(value['error']))
                    return value['result']
            raise TimeoutError('MCP call deadline exceeded')
        try:
            handshake = request(1, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                                 'clientInfo': {'name': 'CodeTrail', 'version': '0.2'}})
            if handshake.get('protocolVersion') != '2025-06-18' or 'tools' not in handshake.get('capabilities', {}):
                raise ValueError('MCP server did not negotiate supported tool protocol')
            send({'method': 'notifications/initialized'})
            tools = request(2, 'tools/list', {})['tools']
            schema = next((t['inputSchema'] for t in tools if t['name'] == tool), None)
            if schema is None or schema.get('type') != 'object':
                raise ValueError('MCP tool not advertised')
            properties = schema.get('properties', {})
            if (any(k not in properties for k in arguments) or any(k not in arguments for k in schema.get('required', []))
                    or any(properties[k].get('type') != 'string' or not isinstance(v, str) or len(v) > 2000 for k, v in arguments.items())):
                raise ValueError('MCP adapter supports only bounded string arguments matching advertised schema')
            result = request(3, 'tools/call', {'name': tool, 'arguments': arguments})
            if result.get('isError'):
                raise RuntimeError('MCP tool returned isError: ' + json.dumps(result))
            return result
        finally:
            if process.poll() is None:
                kill_tree(process)
            process.wait(timeout=10)
            process.stdin.close()
            process.stdout.close()
