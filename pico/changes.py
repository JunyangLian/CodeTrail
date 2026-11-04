"""Bounded edit transactions with compare-before-write and conflict-safe rewind.

Atomic per-file replacement; multi-file effects are journalled, not magically
atomic across a process crash. Existing user changes are the baseline.
"""
import difflib
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from .persistence import write_json_atomic
from .security import redact_text


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def replace_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.codetrail-', delete=False) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class ChangeStore:
    def __init__(self, agent):
        self.agent = agent
        self.root = agent.root / '.pico/changes'
        self.root.mkdir(parents=True, exist_ok=True)

    def prepare(self, changes):
        if not isinstance(changes, list) or not 1 <= len(changes) <= 8:
            raise ValueError('Edits require 1..8 files')
        result, seen = [], set()
        for item in changes:
            path = self.agent.path(item['path'])
            key = path.relative_to(self.agent.root).as_posix()
            if key in seen or any(p.startswith('.codetrail-') for p in path.parts):
                raise ValueError('Duplicate or internal edit path')
            seen.add(key)
            before = path.read_bytes() if path.exists() else None
            if before is not None and len(before) > 262144:
                raise ValueError('Edit file exceeds 256 KiB')
            if 'expected_sha256' not in item or item['expected_sha256'] != digest(before):
                raise ValueError('Edit conflict: expected_sha256 must match current file (null for new file)')
            content = item.get('content')
            if not isinstance(content, str) or len(content.encode()) > 262144:
                raise ValueError('New UTF-8 content required, <= 256 KiB')
            original = before.decode('utf-8') if before is not None else None
            if redact_text(content) != content or (original is not None and redact_text(original) != original):
                raise ValueError('Credential-bearing edits cannot enter the rewind journal')
            preview = ''.join(difflib.unified_diff((original or '').splitlines(True), content.splitlines(True),
                                                 fromfile=key, tofile=key))
            result.append({'path': key, 'before': original, 'after': content,
                           'before_sha256': digest(before), 'after_sha256': digest(content.encode()),
                           'diff': preview[:12000]})
        return result

    def apply(self, changes):
        rows = self.prepare(changes)
        key = 'chg_' + uuid.uuid4().hex
        record = {'id': key, 'session_id': self.agent.session['id'], 'status': 'prepared', 'files': rows}
        target = self.root / (key + '.json')
        write_json_atomic(target, record)
        applied = []
        try:
            for row in rows:
                path = self.agent.path(row['path'])
                current = path.read_bytes() if path.exists() else None
                if digest(current) != row['before_sha256']:
                    raise ValueError('Edit conflict after staging; no overwrite: ' + row['path'])
                replace_bytes(path, row['after'].encode())
                applied.append(row)
        except Exception:
            for row in reversed(applied):
                path = self.agent.path(row['path'])
                if digest(path.read_bytes() if path.exists() else None) != row['after_sha256']:
                    record['status'] = 'conflicted'
                    write_json_atomic(target, record)
                    raise ValueError('Rollback conflict; later user changes preserved')
                if row['before'] is None:
                    path.unlink()
                else:
                    replace_bytes(path, row['before'].encode())
            record['status'] = 'rolled_back'
            write_json_atomic(target, record)
            raise
        record['status'] = 'applied'
        write_json_atomic(target, record)
        return {'change_id': key, 'status': 'applied', 'diffs': [{k: r[k] for k in ('path', 'diff')} for r in rows]}

    def rewind(self, key):
        if not isinstance(key, str) or not key.startswith('chg_') or not key[4:].isalnum():
            raise ValueError('Invalid change ID')
        target = self.root / (key + '.json')
        record = json.loads(target.read_text('utf-8'))
        if record['session_id'] != self.agent.session['id'] or record['status'] != 'applied':
            raise ValueError('Change is not rewindable in this session')
        rows = record['files']
        for row in rows:
            path = self.agent.path(row['path'])
            if digest(path.read_bytes() if path.exists() else None) != row['after_sha256']:
                raise ValueError('Rewind conflict; subsequent edits preserved')
        # Rewind itself uses the same journalled edit protocol for existing files.
        record['status'] = 'rewinding'
        write_json_atomic(target, record)
        for row in rows:
            path = self.agent.path(row['path'])
            if digest(path.read_bytes() if path.exists() else None) != row['after_sha256']:
                raise ValueError('Rewind conflict after preflight')
            if row['before'] is None:
                path.unlink()
            else:
                replace_bytes(path, row['before'].encode())
        record['status'] = 'rewound'
        write_json_atomic(target, record)
        return {'change_id': key, 'status': 'rewound'}
