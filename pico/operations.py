"""Write-ahead risk-operation journal. Unknown effects are never auto-replayed."""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .security import redact_artifact


def snapshot_hash(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


class RecoveryRequired(RuntimeError):
    pass


class OperationJournal:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / '.pico/operations.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, session_id TEXT, run_id TEXT, name TEXT,
                arguments_hash TEXT, arguments TEXT, status TEXT, before_hash TEXT,
                after_hash TEXT, result TEXT, metadata TEXT, updated REAL)''')
            db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT, event TEXT, at REAL, details TEXT)')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def event(self, db, operation_id, event, details=''):
        db.execute('INSERT INTO events VALUES (?,?,?,?)', (operation_id, event, time.time(), details))

    def unresolved(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,session_id,run_id,name,status,before_hash,updated FROM operations WHERE status IN ('awaiting_approval','executing','uncertain')")]

    def begin(self, name, args, before, session_id, run_id, call_id=None):
        operation_id = run_id + ':' + call_id if run_id and call_id else 'op_' + uuid.uuid4().hex
        # Raw values are hashed for binding; only redacted parameters are stored.
        args_hash = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        before_hash = snapshot_hash(before)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM operations WHERE id=?', (operation_id,)).fetchone()
            if previous:
                if previous['arguments_hash'] != args_hash or previous['name'] != name:
                    raise RecoveryRequired('Operation ID was reused with different arguments')
                if previous['status'] == 'completed' and previous['after_hash'] == before_hash:
                    return operation_id, {'content': previous['result'], 'metadata': json.loads(previous['metadata'])}
                raise RecoveryRequired('Existing operation requires inspection; no automatic replay: ' + operation_id)
            pending = db.execute("SELECT id FROM operations WHERE status IN ('awaiting_approval','executing','uncertain') LIMIT 1").fetchone()
            if pending:
                raise RecoveryRequired('Unresolved operation requires operator inspection: ' + pending['id'])
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (operation_id, session_id, run_id, name, args_hash,
                        json.dumps(redact_artifact(args)), 'awaiting_approval', before_hash, '', '', '{}', time.time()))
            self.event(db, operation_id, 'intent_persisted', before_hash)
        return operation_id, None

    def start(self, operation_id, current_snapshot):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM operations WHERE id=?', (operation_id,)).fetchone()
            if not row or row['status'] != 'awaiting_approval':
                raise RecoveryRequired('Approval is absent or already consumed')
            if row['before_hash'] != snapshot_hash(current_snapshot):
                db.execute("UPDATE operations SET status='approval_invalidated',updated=? WHERE id=?", (time.time(), operation_id))
                self.event(db, operation_id, 'approval_invalidated', 'workspace_changed')
                invalid = True
            else:
                db.execute("UPDATE operations SET status='executing',updated=? WHERE id=?", (time.time(), operation_id))
                self.event(db, operation_id, 'execution_started')
                invalid = False
        if invalid:
            raise RecoveryRequired('Workspace changed while awaiting approval; request new approval')

    def reject(self, operation_id):
        with self.connect() as db:
            changed = db.execute("UPDATE operations SET status='rejected',updated=? WHERE id=? AND status='awaiting_approval'", (time.time(), operation_id)).rowcount
            if changed != 1:
                raise RecoveryRequired('Approval is no longer pending')
            self.event(db, operation_id, 'approval_denied')

    def complete(self, operation_id, snapshot, content, metadata, uncertain=False):
        with self.connect() as db:
            changed = db.execute("UPDATE operations SET status=?,after_hash=?,result=?,metadata=?,updated=? WHERE id=? AND status='executing'",
                ('uncertain' if uncertain else 'completed', snapshot_hash(snapshot), content,
                 json.dumps(redact_artifact(metadata)), time.time(), operation_id)).rowcount
            if changed != 1:
                raise RecoveryRequired('Operation is not executing')
            self.event(db, operation_id, 'uncertain' if uncertain else 'result_persisted')

    def abandon(self, operation_id, inspected_hash, reason, current_snapshot):
        """Operator-only reconciliation; does not claim unknown Shell effects succeeded."""
        if not reason.strip() or inspected_hash != snapshot_hash(current_snapshot):
            raise ValueError('Current workspace hash and explicit inspection reason are required')
        with self.connect() as db:
            changed = db.execute("UPDATE operations SET status='abandoned_no_replay',updated=? WHERE id=? AND status IN ('awaiting_approval','executing','uncertain')",
                                 (time.time(), operation_id)).rowcount
            if changed != 1:
                raise RecoveryRequired('Operation is no longer unresolved')
            self.event(db, operation_id, 'operator_abandoned_no_replay', json.dumps({'inspected_hash': inspected_hash, 'reason': reason}))


def main(argv=None):
    from .providers.clients import FakeModelClient
    from .runtime import Pico, SessionStore
    from .workspace import WorkspaceContext
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--abandon')
    parser.add_argument('--inspected-hash')
    parser.add_argument('--reason', default='')
    args = parser.parse_args(argv)
    root = Path(args.workspace).resolve()
    agent = Pico(FakeModelClient([]), WorkspaceContext.build(root, repo_root_override=root), SessionStore(root / '.pico/sessions'))
    snapshot = agent.capture_workspace_snapshot()
    if args.abandon:
        agent.operations.abandon(args.abandon, args.inspected_hash, args.reason, snapshot)
    print(json.dumps({'workspace_hash': snapshot_hash(snapshot), 'unresolved': agent.operations.unresolved()}, indent=2))


if __name__ == '__main__':
    main()
