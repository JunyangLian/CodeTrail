"""Workspace-owned append-only facts, redacted objects, and dependency edges."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from .security import redact_artifact, redact_text


class EvidenceStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path_filter = None
        self.path = self.root / '.pico/evidence.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS facts (seq INTEGER PRIMARY KEY, session TEXT, run TEXT, kind TEXT, payload TEXT, at REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS objects (id TEXT PRIMARY KEY, session TEXT, run TEXT, name TEXT, content TEXT, metadata TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS edges (source TEXT, target TEXT, relation TEXT, PRIMARY KEY(source,target,relation))')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def event(self, session, run, kind, payload):
        with self.connect() as db:
            cursor = db.execute('INSERT INTO facts(session,run,kind,payload,at) VALUES (?,?,?,?,?)',
                                (session, run, kind, json.dumps(redact_artifact(payload)), time.time()))
            return cursor.lastrowid

    def put(self, session, run, name, content, metadata):
        content = redact_text(content)
        identity = json.dumps({'session': session, 'run': run, 'name': name, 'content': content,
                               'metadata': redact_artifact(metadata)}, sort_keys=True)
        key = 'ev_' + hashlib.sha256(identity.encode()).hexdigest()[:32]
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO objects VALUES (?,?,?,?,?,?)',
                       (key, session, run, name, content, json.dumps(redact_artifact(metadata))))
        self.event(session, run, 'evidence_saved', {'id': key, 'tool': name})
        return key

    def get(self, key, session):
        with self.connect() as db:
            row = db.execute('SELECT * FROM objects WHERE id=? AND session=?', (key, session)).fetchone()
        if row is None:
            raise ValueError('Evidence not found in this session')
        value = dict(row)
        value['metadata'] = json.loads(value['metadata'])
        if not self.authorized(value['metadata']):
            raise ValueError('Evidence source is no longer authorized')
        return value

    def authorized(self, metadata):
        paths = list(metadata.get('source_paths', []))
        if metadata.get('args', {}).get('path'):
            paths.append(metadata['args']['path'])
        return not self.path_filter or all(self.path_filter(p) for p in paths)

    def link(self, source, target, relation):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO edges VALUES (?,?,?)', (source, target, relation))

    def find(self, session, query, limit=5):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200 or not 1 <= limit <= 10:
            raise ValueError('Evidence search requires bounded query/limit')
        query = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        with self.connect() as db:
            rows = db.execute("SELECT id,run,name,metadata,substr(content,1,400) preview FROM objects WHERE session=? AND (content LIKE ? ESCAPE '\\' OR metadata LIKE ? ESCAPE '\\') ORDER BY rowid DESC LIMIT ?",
                              (session, '%'+query+'%', '%'+query+'%', limit)).fetchall()
        return [{**dict(r), 'metadata': json.loads(r['metadata']), 'historical': True} for r in rows
                if self.authorized(json.loads(r['metadata']))]

    def events(self, session, limit=100):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM facts WHERE session=? ORDER BY seq DESC LIMIT ?', (session, limit)).fetchall()
        return [{**dict(row), 'payload': json.loads(row['payload'])} for row in reversed(rows)]


def update_plan(agent, steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
        raise ValueError('Plan requires 1..12 steps')
    seen = set()
    clean = []
    old = {s['id']: s for s in agent.session.get('plan', [])}
    for item in steps:
        key = item.get('id')
        if not isinstance(key, str) or not key or key in seen or len(key) > 40:
            raise ValueError('Unique bounded step IDs required')
        dependencies = item.get('depends_on', [])
        status = item.get('status', 'pending')
        if status not in {'pending', 'running', 'completed', 'blocked'} or not isinstance(dependencies, list):
            raise ValueError('Invalid plan status/dependencies')
        if any(d not in seen for d in dependencies):
            raise ValueError('Dependencies must refer to earlier steps')
        title, acceptance = item.get('title'), item.get('acceptance')
        if not all(isinstance(v, str) and 0 < len(v) <= 300 for v in (title, acceptance)):
            raise ValueError('Each step needs title and acceptance condition')
        refs = item.get('evidence', [])
        if not isinstance(refs, list) or len(refs) > 8:
            raise ValueError('Invalid step evidence')
        evidence_rows = [agent.evidence.get(ref, agent.session['id']) for ref in refs]
        kind = item.get('kind', 'investigation')
        if kind not in {'investigation', 'edit', 'verification'}:
            raise ValueError('Unknown plan step kind')
        if status == 'completed' and not refs:
            raise ValueError('Completed steps require real evidence references')
        if status == 'completed' and kind == 'verification':
            from .operations import snapshot_hash
            current = snapshot_hash(agent.capture_workspace_snapshot())
            if not any(r['name'] == 'run_shell' and r['metadata'].get('exit_code') == 0 and
                       not r['metadata'].get('workspace_changed') and
                       r['metadata'].get('tool_status') == 'ok' and r['metadata'].get('snapshot_sha256') == current and
                       r['metadata'].get('args', {}).get('command') == agent.verification_command for r in evidence_rows):
                raise ValueError('Verification steps require current successful configured test evidence')
        if status in {'running', 'completed'} and any(next(s for s in clean if s['id'] == d)['status'] != 'completed' for d in dependencies):
            raise ValueError('Dependencies have not completed')
        clean.append({'id': key, 'title': title, 'acceptance': acceptance, 'depends_on': dependencies,
                      'status': status, 'kind': kind, 'evidence': refs, 'status_origin': 'model_with_evidence',
                      'prior_status': old.get(key, {}).get('status')})
        seen.add(key)
    agent.evidence.event(agent.session['id'], getattr(agent.current_task_state, 'run_id', ''), 'plan_updated', clean)
    agent.session['plan'] = redact_artifact(clean)
    agent.session_path = agent.session_store.save(agent.session)
    return clean


def restore_session(agent):
    """Recover profile history/plan projections; never replay a tool effect."""
    events = agent.evidence.events(agent.session['id'], 100000)
    if not any(e['kind'] in {'session_import', 'session_record', 'session_reset'} for e in events):
        agent.evidence.event(agent.session['id'], '', 'session_import', agent.session.get('history', []))
        return
    history, plan = [], agent.session.get('plan', [])
    for event in events:
        if event['kind'] == 'session_import':
            history = event['payload']
        elif event['kind'] == 'session_reset':
            history, plan = [], []
        elif event['kind'] == 'session_record':
            history.append(event['payload'])
        elif event['kind'] == 'plan_updated':
            plan = event['payload']
    agent.session['history'], agent.session['plan'] = history, plan


def invalidate_plan(agent):
    """A completed step is not current evidence after its dependencies change."""
    from .operations import snapshot_hash
    changed = []
    plan = agent.session.get('plan', [])
    current = None
    for step in plan:
        if step.get('status') != 'completed':
            continue
        stale = False
        for ref in step.get('evidence', []):
            try:
                row = agent.evidence.get(ref, agent.session['id'])
                meta = row['metadata']
                path = meta.get('args', {}).get('path')
                if meta.get('source_sha256') and path:
                    stale |= hashlib.sha256(agent.path(path).read_bytes()).hexdigest() != meta['source_sha256']
                if step.get('kind') == 'verification':
                    if current is None:
                        current = snapshot_hash(agent.capture_workspace_snapshot())
                    stale |= meta.get('snapshot_sha256') != current
            except (ValueError, OSError):
                stale = True
        if any(d in changed for d in step.get('depends_on', [])):
            stale = True
        if stale:
            step['status'], step['invalidated_reason'] = 'pending', 'source_or_dependency_changed'
            changed.append(step['id'])
    if changed:
        agent.evidence.event(agent.session['id'], '', 'plan_updated', plan)
    return changed
