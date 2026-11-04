from concurrent.futures import ThreadPoolExecutor
import subprocess
import json
import os
from pathlib import Path
import sys

import pytest

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.operations import OperationJournal, RecoveryRequired, snapshot_hash


def agent_at(path):
    return Pico(FakeModelClient([]), WorkspaceContext.build(path, repo_root_override=path),
                SessionStore(path / '.pico/sessions'), approval_policy='auto')


def test_crash_after_file_effect_blocks_replay_until_explicit_operator_reconciliation(tmp_path):
    (tmp_path / 'app.py').write_text('before')
    first = agent_at(tmp_path)
    def crash(args):
        (tmp_path / 'app.py').write_text('effect happened')
        raise SystemExit('simulated process death after effect, before result persistence')
    first.tools['write_file']['run'] = crash
    with pytest.raises(SystemExit):
        first.execute_tool('write_file', {'path': 'app.py', 'content': 'requested'})
    pending = first.operations.unresolved()
    assert len(pending) == 1 and pending[0]['status'] == 'executing'
    second = agent_at(tmp_path)
    result = second.execute_tool('write_file', {'path': 'app.py', 'content': 'must not execute'})
    assert result.metadata['tool_error_code'] == 'operation_recovery_required'
    assert (tmp_path / 'app.py').read_text() == 'effect happened'
    current = second.capture_workspace_snapshot()
    with pytest.raises(ValueError):
        second.operations.abandon(pending[0]['id'], 'incorrect hash', 'inspected files', current)
    second.operations.abandon(pending[0]['id'], snapshot_hash(current), 'operator inspected; unknown original result, no replay', current)
    assert second.execute_tool('write_file', {'path': 'app.py', 'content': 'fresh approved request'}).metadata['tool_status'] == 'ok'


def test_workspace_change_during_approval_invalidates_the_approval(tmp_path):
    (tmp_path / 'app.py').write_text('original')
    agent = agent_at(tmp_path)
    def approve(name, args):
        (tmp_path / 'app.py').write_text('user intervened')
        return True
    agent.approve = approve
    result = agent.execute_tool('patch_file', {'path': 'app.py', 'old_text': 'original', 'new_text': 'agent edit'})
    assert result.metadata['tool_error_code'] == 'approval_snapshot_changed'
    assert (tmp_path / 'app.py').read_text() == 'user intervened'
    assert not agent.operations.unresolved()


def test_crash_while_waiting_does_not_restore_old_approval(tmp_path):
    agent = agent_at(tmp_path)
    def crash(*args): raise SystemExit('approval wait interrupted')
    agent.approve = crash
    with pytest.raises(SystemExit):
        agent.execute_tool('write_file', {'path': 'app.py', 'content': 'never written'})
    assert agent.operations.unresolved()[0]['status'] == 'awaiting_approval'
    second = agent_at(tmp_path)
    assert second.execute_tool('write_file', {'path': 'app.py', 'content': 'never written'}).metadata['tool_status'] == 'rejected'
    assert not (tmp_path / 'app.py').exists()


def test_completed_operation_reuses_only_matching_id_arguments_and_final_snapshot(tmp_path):
    journal = OperationJournal(tmp_path)
    operation, cached = journal.begin('patch_file', {'path': 'a'}, {'a': 'before'}, 'session', 'run', 'native-id')
    assert cached is None
    journal.start(operation, {'a': 'before'})
    journal.complete(operation, {'a': 'after'}, 'real result', {'tool_status': 'ok'})
    assert journal.begin('patch_file', {'path': 'a'}, {'a': 'after'}, 'session', 'run', 'native-id')[1]['content'] == 'real result'
    with pytest.raises(RecoveryRequired):
        journal.begin('patch_file', {'path': 'b'}, {'a': 'after'}, 'session', 'run', 'native-id')
    with pytest.raises(RecoveryRequired):
        journal.begin('patch_file', {'path': 'a'}, {'a': 'changed externally'}, 'session', 'run', 'native-id')


def test_shell_timeout_is_uncertain_even_without_file_changes(tmp_path):
    agent = agent_at(tmp_path)
    def timeout(args): raise subprocess.TimeoutExpired('public test command', 1)
    agent.tools['run_shell']['run'] = timeout
    result = agent.execute_tool('run_shell', {'command': 'python -m unittest'})
    assert result.metadata['tool_error_code'] == 'tool_timeout'
    assert agent.operations.unresolved()[0]['status'] == 'uncertain'


def test_denial_persists_without_side_effect_and_does_not_block_fresh_approval(tmp_path):
    agent = agent_at(tmp_path)
    agent.approve = lambda *args: False
    result = agent.execute_tool('write_file', {'path': 'a', 'content': 'denied'})
    assert result.metadata['tool_error_code'] == 'approval_denied' and not agent.operations.unresolved()
    agent.approve = lambda *args: True
    assert agent.execute_tool('write_file', {'path': 'a', 'content': 'allowed'}).metadata['tool_status'] == 'ok'


def test_parallel_writers_cannot_both_reserve_the_same_workspace(tmp_path):
    OperationJournal(tmp_path)
    def reserve(index):
        try:
            return OperationJournal(tmp_path).begin('write_file', {'index': index}, {}, str(index), 'run', str(index))[0]
        except RecoveryRequired:
            return None
    with ThreadPoolExecutor(2) as pool:
        assert sum(value is not None for value in pool.map(reserve, (1, 2))) == 1


def test_actual_process_exit_after_effect_leaves_durable_unresolved_operation(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = '''import os,sys
from pathlib import Path
sys.path.insert(0, ROOT)
from pico import Pico,FakeModelClient,SessionStore,WorkspaceContext
p=Path(WORKSPACE)
a=Pico(FakeModelClient([]),WorkspaceContext.build(p,repo_root_override=p),SessionStore(p/'.pico/sessions'),approval_policy='auto')
def crash(args):
 (p/'result.txt').write_text('effect before exit')
 os._exit(77)
a.tools['write_file']['run']=crash
a.execute_tool('write_file',{'path':'result.txt','content':'effect'})
'''.replace('ROOT', repr(str(root))).replace('WORKSPACE', repr(str(tmp_path)))
    env = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code], env=env, capture_output=True, timeout=20)
    assert result.returncode == 77, result.stderr.decode(errors='replace')
    restarted = agent_at(tmp_path)
    assert restarted.operations.unresolved()[0]['status'] == 'executing'
    assert restarted.execute_tool('write_file', {'path': 'result.txt', 'content': 'duplicate'}).metadata['tool_error_code'] == 'operation_recovery_required'
    assert (tmp_path / 'result.txt').read_text() == 'effect before exit'
