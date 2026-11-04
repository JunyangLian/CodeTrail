"""Offline mechanism demonstration, not a model benchmark or an Agent repair score.

Run: python -X utf8 scripts/demo_long_task.py --output artifacts/your-new-long-task-demo
The output directory must be new. No API credentials, network, or frozen tasks.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.knowledge import KnowledgeStore
from pico.native_context import NativeContextBudget, protocol_groups
from pico.operations import snapshot_hash
from pico.providers.native_tools import NativeToolConversation
from pico.tools import BASE_TOOL_SPECS


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def agent(workspace):
    return Pico(FakeModelClient([]), WorkspaceContext.build(workspace, repo_root_override=workspace),
                SessionStore(workspace / '.pico/sessions'), approval_policy='ask')


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    sources = sorted((ROOT / 'pico').rglob('*.py')) + [Path(__file__).resolve()]
    save(output / 'protocol.json', {
        'kind': 'offline mechanism validation; not model capability or benchmark score',
        'api_calls': 0, 'frozen_tasks_used': 0,
        'expected': ['source_index_survives_new_session', 'candidate_not_recalled',
                     'operator_confirmation', 'ttl_expiry', 'source_change_invalidation',
                     'new_structure_reindexed', 'actual_native_payload_budget', 'complete_tool_pairs',
                     'original_user_constraint_retained', 'uncertain_effect_not_replayed',
                     'explicit_operator_reconciliation', 'fresh_approval_after_reconciliation'],
        'source_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    })
    checks = []
    def check(name, condition, evidence):
        checks.append({'name': name, 'passed': bool(condition), 'evidence': evidence})
        save(output / 'checks.json', checks)
        if not condition:
            raise AssertionError(name)
    workspace = output / 'workspace'
    workspace.mkdir()
    source = workspace / 'parser.py'
    source.write_text('# Module fixture\n' * 6 + 'def parse(value: str) -> int:\n    return int(value)\n', encoding='utf-8')
    first = agent(workspace)
    first.execute_tool('read_file', {'path': 'parser.py'})
    second = agent(workspace)
    notes = second.memory.retrieval_candidates('parser parse')
    check('source_index_survives_new_session', first.session['id'] != second.session['id'] and
          any(n.get('kind') == 'verified' and 'parse' in n['text'] for n in notes), notes)

    clock = [time.time()]
    store = KnowledgeStore(workspace, clock=lambda: clock[0])
    candidate = store.propose('fixture convention: use typed parse parameters', 'fixture-convention', ['parser.py'], ttl_seconds=5)
    check('candidate_not_recalled', all(n['id'] != candidate['id'] for n in store.search('fixture convention')), candidate['id'])
    activated = store.activate(candidate['id'], 1, reviewer='offline_fixture_operator')
    check('operator_confirmation', any(n['id'] == candidate['id'] for n in store.search('fixture convention')), activated)
    clock[0] += 6
    check('ttl_expiry', all(n['id'] != candidate['id'] for n in store.search('fixture convention')) and
          store.get(candidate['id'])['status'] == 'expired', store.get(candidate['id'])['status'])

    old_id = next(n['memory_id'] for n in notes if n.get('kind') == 'verified')
    # Fixture-controller edit represents a user changing source between tasks.
    source.write_text('def decode(value, default=0):\n    return value if value is not None else default\n', encoding='utf-8')
    check('source_change_invalidation', not any(n['id'] == old_id for n in store.search('parser parse')),
          store.get(old_id)['status'])
    second.execute_tool('read_file', {'path': 'parser.py'})
    check('new_structure_reindexed', any('decode' in n['text'] and n.get('kind') == 'verified'
          for n in agent(workspace).memory.retrieval_candidates('decode')), 'new AST source hash and symbol')

    conversation = NativeToolConversation(BASE_TOOL_SPECS, 'fixture workspace', 'Never modify tests; inspect source', None)
    conversation.context_budget = NativeContextBudget(18000, 65536)
    conversation.context_state = {'verification': {'status': 'failed', 'exit_code': 1}, 'changed_files': ['parser.py']}
    conversation.set_budget(4)
    for index in range(16):
        call = {'id': 'fixture-call-' + str(index), 'type': 'function',
                'function': {'name': 'read_file', 'arguments': '{"path":"parser.py"}'}}
        # Scripted provider frames are explicitly fixtures, not generated answers.
        conversation.accept({'choices': [{'message': {'content': None, 'tool_calls': [call],
            'reasoning_content': 'public fixture placeholder'}}]}, {})
        conversation.observe('public repeated fixture output\n' * 200)
    payload = conversation.prepare_payload({'model': 'offline-fixture', 'max_tokens': 1024, **conversation.payload()})
    save(output / 'prepared-request.json', payload)
    save(output / 'context-audit.json', conversation.last_context_audit)
    check('actual_native_payload_budget', len(json.dumps(payload).encode()) <= 18000 and
          conversation.last_context_audit['compacted_groups'] > 0, conversation.last_context_audit)
    protocol_groups(payload['messages'][4:])
    check('complete_tool_pairs', payload['messages'][-1]['tool_call_id'] == 'fixture-call-15', 'latest actual fixture ID retained')
    check('original_user_constraint_retained', 'Never modify tests' in payload['messages'][1]['content'], payload['messages'][1]['content'])

    third = agent(workspace)
    third.approve = lambda *args: True  # Explicit fixture operator, not model approval.
    def interrupted_write(args):
        (workspace / 'effect.txt').write_text('effect occurred before interruption', encoding='utf-8')
        raise SystemExit('fixture interruption after effect')
    third.tools['write_file']['run'] = interrupted_write
    try:
        third.execute_tool('write_file', {'path': 'effect.txt', 'content': 'fixture effect'})
    except SystemExit:
        pass
    restarted = agent(workspace)
    blocked = restarted.execute_tool('write_file', {'path': 'effect.txt', 'content': 'must not replay'})
    check('uncertain_effect_not_replayed', blocked.metadata.get('tool_error_code') == 'operation_recovery_required' and
          (workspace / 'effect.txt').read_text(encoding='utf-8') == 'effect occurred before interruption', restarted.operations.unresolved())
    operation = restarted.operations.unresolved()[0]
    snapshot = restarted.capture_workspace_snapshot()
    restarted.operations.abandon(operation['id'], snapshot_hash(snapshot), 'fixture operator inspected actual file; no replay', snapshot)
    check('explicit_operator_reconciliation', restarted.operations.unresolved() == [], operation['id'])
    approvals = []
    restarted.approve = lambda name, args: approvals.append(name) or True
    fresh = restarted.execute_tool('write_file', {'path': 'effect.txt', 'content': 'new explicitly approved request'})
    check('fresh_approval_after_reconciliation', approvals == ['write_file'] and fresh.metadata['tool_status'] == 'ok', fresh.metadata)
    save(output / 'summary.json', {'kind': 'offline mechanism validation only', 'checks': len(checks),
                                 'passed': sum(c['passed'] for c in checks), 'api_calls': 0})
    print(json.dumps({'output': str(output), 'checks': len(checks), 'passed': sum(c['passed'] for c in checks), 'api_calls': 0}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.output.resolve())


if __name__ == '__main__':
    main()
