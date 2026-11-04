"""Bundled read-only MCP demo. No secrets, subprocesses, network or imports from repo."""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
for line in sys.stdin.buffer:
    request = {}
    try:
        if len(line) > 16384:
            raise ValueError('Input too large')
        request = json.loads(line)
        method = request['method']
        if method == 'notifications/initialized':
            continue
        if method == 'initialize':
            result = {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'codetrail-docs', 'version': '1'}}
        elif method == 'tools/list':
            result = {'tools': [{'name': 'search_docs', 'description': 'Read-only keyword search in repository Markdown documentation',
                       'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query'],
                                       'additionalProperties': False}, 'annotations': {'readOnlyHint': True}}]}
        elif method == 'tools/call' and request['params']['name'] == 'search_docs':
            query = request['params']['arguments']['query'].casefold()
            if not query.strip() or len(query) > 2000:
                raise ValueError('Invalid query')
            hits = []
            paths = [root / 'README.md'] + sorted((root / 'docs').glob('*.md'))[:30]
            allowed = set(json.loads(sys.argv[2])) if len(sys.argv) > 2 else None
            for path in paths:
                if (not path.is_file() or path.is_symlink() or path.stat().st_size > 131072 or path.stat().st_nlink > 1
                        or (allowed is not None and path.relative_to(root).as_posix() not in allowed)):
                    continue
                path.resolve().relative_to(root)
                text = path.read_text('utf-8')
                for n, text_line in enumerate(text.splitlines(), 1):
                    if query in text_line.casefold():
                        hits.append(f'{path.relative_to(root).as_posix()}:{n}: {text_line[:300]}')
                        if len(hits) >= 12:
                            break
                if len(hits) >= 12:
                    break
            result = {'content': [{'type': 'text', 'text': '\n'.join(hits) or '(no matching documentation)'}]}
        else:
            raise ValueError('Unsupported MCP method/tool')
        reply = {'jsonrpc': '2.0', 'id': request.get('id'), 'result': result}
    except Exception as exc:
        reply = {'jsonrpc': '2.0', 'id': request.get('id'), 'error': {'code': -32602, 'message': str(exc)}}
    sys.stdout.write(json.dumps(reply, ensure_ascii=True) + '\n')
    sys.stdout.flush()
