"""Evidence-bound repository knowledge with explicit promotion and invalidation.

SQLite transactions protect status transitions and optimistic versions. Facts
are advisory data, never tool policy. Only mechanical Python structure may be
automatically activated; prose always needs explicit operator confirmation.
"""
import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .security import redact_text, sensitive_workspace_path


def source_structure(text):
    tree = ast.parse(text)
    symbols = []
    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append({'kind': 'function', 'name': item.name, 'arguments': ast.unparse(item.args),
                            'returns': ast.unparse(item.returns) if item.returns else None, 'line': item.lineno})
        elif isinstance(item, ast.ClassDef):
            symbols.append({'kind': 'class', 'name': item.name, 'line': item.lineno})
        elif isinstance(item, ast.ImportFrom):
            symbols.append({'kind': 'import', 'module': '.' * item.level + (item.module or ''),
                            'names': [alias.name for alias in item.names], 'line': item.lineno})
        elif isinstance(item, ast.Import):
            symbols.append({'kind': 'import', 'names': [alias.name for alias in item.names], 'line': item.lineno})
        if len(symbols) >= 24:
            break
    return json.dumps({'type': 'python_structure_not_semantic_summary', 'symbols': symbols}, ensure_ascii=False)


class KnowledgeStore:
    def __init__(self, workspace, database=None, clock=time.time):
        self.root = Path(workspace).resolve()
        self.path = Path(database) if database else self.root / '.pico/memory/knowledge.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.owner = hashlib.sha256(os.path.normcase(str(self.root)).encode()).hexdigest()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS ownership (owner TEXT PRIMARY KEY)')
            owners = db.execute('SELECT owner FROM ownership').fetchall()
            if owners and owners[0][0] != self.owner:
                raise ValueError('Knowledge database belongs to another workspace')
            db.execute('INSERT OR IGNORE INTO ownership VALUES (?)', (self.owner,))
            db.execute('''CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, fact_key TEXT NOT NULL, text TEXT NOT NULL, scope TEXT NOT NULL,
                session_id TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
                evidence TEXT NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL,
                expires REAL, reviewer TEXT NOT NULL DEFAULT '')''')
            db.execute('CREATE TABLE IF NOT EXISTS events (at REAL, id TEXT, event TEXT, version INTEGER, reason TEXT)')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def evidence(self, path):
        raw = Path(path)
        target = (self.root / raw).resolve()
        relative = target.relative_to(self.root)
        if sensitive_workspace_path(relative) or any(p.casefold() in {'.git', '.pico'} for p in relative.parts):
            raise ValueError('Sensitive or internal memory evidence denied')
        if not target.is_file() or target.stat().st_size > 262144:
            raise ValueError('Memory evidence must be a regular file <= 256 KiB')
        data = target.read_bytes()
        return {'path': relative.as_posix(), 'sha256': hashlib.sha256(data).hexdigest()}

    @staticmethod
    def safe_text(text):
        if (not isinstance(text, str) or not text.strip() or len(text) > 6000 or
                redact_text(text) != text or '<redacted>' in text or
                re.search(r'(?i)\b(?:sk-|ghp_|github_pat_)[\w-]{6,}', text)):
            raise ValueError('Invalid or credential-shaped memory text')

    def propose(self, text, fact_key, evidence_paths=(), scope='repository', session_id='', ttl_seconds=None, kind='prose'):
        self.safe_text(text)
        self.safe_text(fact_key)
        if kind not in {'prose', 'source_index'}:
            raise ValueError('Unknown memory kind')
        if scope not in {'repository', 'session'} or (scope == 'session' and not session_id):
            raise ValueError('Session memory requires an explicit session ID')
        if ttl_seconds is not None and (isinstance(ttl_seconds, bool) or ttl_seconds <= 0):
            raise ValueError('TTL must be positive')
        evidence = [self.evidence(p) for p in evidence_paths]
        memory_id = 'mem_' + uuid.uuid4().hex
        at = self.clock()
        with self.connect() as db:
            db.execute('INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (memory_id, fact_key, text, scope, session_id if scope == 'session' else '',
                        'candidate', 1, json.dumps(evidence), kind, at,
                        at + ttl_seconds if ttl_seconds is not None else None, ''))
            self.event(db, memory_id, 'proposed', 1)
        return self.get(memory_id)

    def event(self, db, memory_id, event, version, reason=''):
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (self.clock(), memory_id, event, version, reason))

    def get(self, memory_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
        if row is None:
            raise KeyError(memory_id)
        result = dict(row)
        result['evidence'] = json.loads(result['evidence'])
        return result

    def fresh(self, record):
        if record['expires'] is not None and record['expires'] <= self.clock():
            return False
        try:
            return all(self.evidence(item['path']) == item for item in record['evidence'])
        except (ValueError, OSError):
            return False

    def activate(self, memory_id, version, reviewer=None, structural=False):
        # This is a host API, deliberately absent from model tool schemas.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
            if row is None:
                raise KeyError(memory_id)
            record = dict(row)
            record['evidence'] = json.loads(record['evidence'])
            if record['version'] != version or record['status'] != 'candidate':
                raise ValueError('Memory version/status conflict')
            if not self.fresh(record):
                raise ValueError('Stale evidence or expired candidate')
            if structural:
                if record['kind'] != 'source_index' or len(record['evidence']) != 1:
                    raise ValueError('Automatic activation requires mechanical source evidence')
                path = self.root / record['evidence'][0]['path']
                if source_structure(path.read_text(encoding='utf-8')) != record['text']:
                    raise ValueError('Source structure does not match candidate')
                reviewer = 'mechanical_ast_check'
            elif not reviewer:
                raise ValueError('Prose activation requires explicit operator confirmation')
            conflicts = db.execute("SELECT id,version FROM memories WHERE fact_key=? AND scope=? AND session_id=? AND status='active'",
                                   (record['fact_key'], record['scope'], record['session_id'])).fetchall()
            for conflict in conflicts:
                db.execute("UPDATE memories SET status='superseded',version=version+1 WHERE id=?", (conflict['id'],))
                self.event(db, conflict['id'], 'superseded', conflict['version'] + 1, memory_id)
            db.execute("UPDATE memories SET status='active',version=version+1,reviewer=? WHERE id=?", (reviewer, memory_id))
            self.event(db, memory_id, 'activated', version + 1, reviewer)
        return self.get(memory_id)

    def retract(self, memory_id, version, reason='operator_retracted'):
        with self.connect() as db:
            changed = db.execute("UPDATE memories SET status='retracted',version=version+1 WHERE id=? AND version=? AND status IN ('candidate','active')",
                                 (memory_id, version)).rowcount
            if changed != 1:
                raise ValueError('Memory version/status conflict')
            self.event(db, memory_id, 'retracted', version + 1, reason)

    def active(self, session_id=''):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT * FROM memories WHERE status='active' AND (scope='repository' OR session_id=?) ORDER BY created DESC", (session_id,)).fetchall()
            result = []
            for row in rows:
                record = dict(row)
                record['evidence'] = json.loads(record['evidence'])
                if not self.fresh(record):
                    status = 'expired' if record['expires'] is not None and record['expires'] <= self.clock() else 'stale'
                    db.execute('UPDATE memories SET status=?,version=version+1 WHERE id=?', (status, record['id']))
                    self.event(db, record['id'], status, record['version'] + 1)
                else:
                    result.append(record)
        return result

    def search(self, query, session_id='', limit=3):
        from .features.memory import _tokenize
        query_tokens = _tokenize(query)
        ranked = []
        for record in self.active(session_id):
            overlap = len(query_tokens & _tokenize(record['text'] + ' ' + record['fact_key']))
            if overlap:
                ranked.append((overlap, record['created'], record))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [r for _, _, r in ranked[:limit]]

    def index_file(self, path):
        evidence = self.evidence(path)
        target = self.root / evidence['path']
        if target.suffix != '.py':
            return None
        text = source_structure(target.read_text(encoding='utf-8'))
        for previous in self.active():
            if previous['fact_key'] == 'source:' + evidence['path'] and previous['text'] == text and previous['evidence'] == [evidence]:
                return previous
        candidate = self.propose(text, 'source:' + evidence['path'], [path], kind='source_index')
        return self.activate(candidate['id'], candidate['version'], structural=True)
