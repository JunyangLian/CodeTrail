import json
import sys

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext

COMMAND = 'python -B -m unittest discover -v'

def call(name, **args):
    return '<tool>'+json.dumps({'name':name,'args':args})+'</tool>'

def agent_at(path, outputs, **kwargs):
    (path/'value.py').write_text('VALUE = 0\n',encoding='utf-8')
    (path/'test_value.py').write_text('import unittest\nfrom value import VALUE\nclass T(unittest.TestCase):\n def test_value(self): self.assertEqual(VALUE,1)\n',encoding='utf-8')
    return Pico(FakeModelClient(outputs),WorkspaceContext.build(path,repo_root_override=path),
                SessionStore(path/'.pico/sessions'),approval_policy='auto',max_steps=kwargs.pop('max_steps',12),
                verification_command=COMMAND,**kwargs)

def events(agent):
    return [json.loads(line) for line in agent.run_store.trace_path(agent.current_task_state).read_text(encoding='utf-8').splitlines()]

def test_plain_progress_is_retry_instead_of_terminal_success():
    assert Pico.parse('我先读取测试，再修复。')[0]=='retry'
    assert Pico.parse('<final>Result.</final>')==('final','Result.')

def test_final_requires_real_passing_test_and_failed_contract_can_be_corrected(tmp_path):
    a=agent_at(tmp_path,[
        '<final>我先修复。</final>',call('patch_file',path='value.py',old_text='VALUE = 0',new_text='VALUE = 2'),
        call('run_shell',command=COMMAND),'<final>Done.</final>',
        call('patch_file',path='value.py',old_text='VALUE = 2',new_text='VALUE = 1'),
        call('run_shell',command=COMMAND),'<final>Verified.</final>'])
    assert a.ask('Repair value.py')=='Verified.'
    assert a.current_task_state.status=='completed'
    rejected=[e for e in events(a) if e['event']=='completion_rejected']
    assert [e['status'] for e in rejected]==['not_run','failed']
    assert a.run_store.load_report(a.current_task_state.run_id)['completion_verification']['status']=='passed'

def test_successful_test_becomes_stale_after_edit_even_if_edit_is_correct(tmp_path):
    a=agent_at(tmp_path,[
        call('patch_file',path='value.py',old_text='VALUE = 0',new_text='VALUE = 1'),
        call('run_shell',command=COMMAND),
        call('patch_file',path='value.py',old_text='VALUE = 1',new_text='VALUE = 1  # comment'),
        '<final>Done.</final>',call('run_shell',command=COMMAND),'<final>Retested.</final>'])
    assert a.ask('Repair')=='Retested.'
    assert any(e['event']=='completion_rejected' and e['status']=='stale' for e in events(a))

def test_verifier_denial_stops_without_automatically_running_it(tmp_path):
    a=agent_at(tmp_path,[call('run_shell',command=COMMAND),'<final>Done.</final>'])
    approvals=[]
    a.approve=lambda name,args: approvals.append(name) or False
    a.ask('Repair')
    assert a.current_task_state.status=='stopped'
    assert a.current_task_state.stop_reason=='verification_incomplete'
    assert approvals==['run_shell']
    assert not any(e.get('exit_code')==0 for e in events(a))

def test_tool_budget_finalization_cannot_bypass_gate(tmp_path):
    a=agent_at(tmp_path,[call('read_file',path='value.py'),'<final>Done.</final>'],max_steps=1)
    a.ask('Repair')
    assert a.current_task_state.status=='stopped'
    assert a.current_task_state.stop_reason=='verification_incomplete'

def test_read_only_information_request_is_not_forced_to_execute_shell(tmp_path):
    a=agent_at(tmp_path,['<final>VALUE is zero.</final>'],read_only=True)
    assert a.ask('Explain VALUE')=='VALUE is zero.'
    assert a.current_task_state.status=='completed'
    assert a.last_verification['status']=='not_required'

def test_other_passing_command_does_not_satisfy_configured_verifier(tmp_path):
    a=agent_at(tmp_path,[call('run_shell',command=f'"{sys.executable}" -c "pass"'),'<final>Done.</final>'],max_steps=1)
    a.ask('Repair')
    assert a.current_task_state.stop_reason=='verification_incomplete'

def test_unchanged_workspace_with_passing_tests_can_complete_without_forced_edit(tmp_path):
    a=agent_at(tmp_path,[call('run_shell',command=COMMAND),'<final>Already correct.</final>'])
    (tmp_path/'value.py').write_text('VALUE = 1\n',encoding='utf-8')
    assert a.ask('Check current value')=='Already correct.'
    assert a.current_task_state.status=='completed'
