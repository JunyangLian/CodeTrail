import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from pico.providers.clients import FakeModelClient, OpenAICompatibleModelClient
from pico.providers.native_tools import NativeToolConversation
from pico.providers.streaming import read_chat_stream
from pico.runtime import Pico, SessionStore
from pico.workspace import WorkspaceContext


def agent(root, outputs=(), **kwargs):
    return Pico(FakeModelClient(list(outputs)), WorkspaceContext.build(root, repo_root_override=root),
                SessionStore(root / '.pico/sessions'), harness_profile='workbench', approval_policy='auto',
                max_depth=0, **kwargs)


def test_profile_preserves_classic_and_enforces_internal_boundary(tmp_path):
    a = agent(tmp_path)
    assert {'code_search', 'apply_changes', 'memory_propose', 'mcp_docs', 'update_plan'} <= a.tools.keys()
    result = a.execute_tool('read_file', {'path': '.pico/operations.sqlite3'})
    assert result.metadata['tool_status'] == 'rejected'
    classic = Pico(FakeModelClient([]), WorkspaceContext.build(tmp_path), SessionStore(tmp_path/'.pico/sessions'), max_depth=0)
    assert len(classic.tools) == 6


def test_rules_scoped_override_and_untruncated_budget(tmp_path):
    from pico.instructions import discover_rules, render_rules
    (tmp_path/'AGENTS.md').write_text('Root policy\n' + 'x'*1500 + '\nTail must survive')
    child = tmp_path/'src'; child.mkdir()
    (child/'AGENTS.md').write_text('overridden')
    (child/'AGENTS.override.md').write_text('Leaf policy')
    rules = discover_rules(tmp_path, child)
    assert [r['path'] for r in rules] == ['AGENTS.md', 'src/AGENTS.override.md']
    assert 'Tail must survive' in render_rules(rules)
    with pytest.raises(ValueError, match='budget'):
        discover_rules(tmp_path, child, max_bytes=100)


def test_multi_file_edit_conflict_rewind_and_no_clobber(tmp_path):
    (tmp_path/'a.py').write_text('existing user change\n')
    (tmp_path/'b.py').write_text('b=1\n')
    a = agent(tmp_path)
    changes = [{'path': p, 'expected_sha256': hashlib.sha256((tmp_path/p).read_bytes()).hexdigest(), 'content': text}
               for p, text in [('a.py', 'a=2\n'), ('b.py', 'b=2\n')]]
    bad = [*changes[:-1], {**changes[-1], 'expected_sha256': 'wrong'}]
    assert a.execute_tool('apply_changes', {'changes': json.dumps(bad)}).metadata['tool_status'] == 'rejected'
    assert (tmp_path/'a.py').read_text() == 'existing user change\n'
    result = a.execute_tool('apply_changes', {'changes': json.dumps(changes)})
    key = json.loads(result.content.split('\nEvidence ID:')[0])['change_id']
    assert (tmp_path/'a.py').read_text() == 'a=2\n'
    (tmp_path/'a.py').write_text('new user edit\n')
    assert a.execute_tool('rewind_changes', {'change_id': key}).metadata['tool_status'] == 'error'
    assert (tmp_path/'b.py').read_text() == 'b=2\n'
    (tmp_path/'a.py').write_bytes(b'a=2\n')
    assert a.execute_tool('rewind_changes', {'change_id': key}).metadata['tool_status'] == 'ok'
    assert (tmp_path/'a.py').read_text() == 'existing user change\n'


def test_policy_hook_denies_before_effect_and_evidence_is_session_bound(tmp_path):
    (tmp_path/'a.py').write_text('x=1\n')
    a = agent(tmp_path)
    (tmp_path/'.pico/policy.json').write_text(json.dumps({'deny_tools': ['patch_file']}))
    result = a.execute_tool('patch_file', {'path':'a.py','old_text':'x=1','new_text':'x=2'})
    assert result.metadata['tool_error_code'] == 'policy_hook_denied'
    read = a.execute_tool('read_file', {'path':'a.py'})
    key = read.metadata['evidence_id']
    assert a.execute_tool('recall_evidence', {'evidence_id':key}).metadata['tool_status'] == 'ok'
    assert agent(tmp_path).execute_tool('recall_evidence', {'evidence_id':key}).metadata['tool_status'] == 'error'
    (tmp_path/'a.py').write_text('x=3\n')
    recalled = a.execute_tool('recall_evidence', {'evidence_id':key})
    assert '"source_current": false' in recalled.content


def test_plan_requires_real_evidence_and_rebuilds_from_ledger(tmp_path):
    (tmp_path/'a.py').write_text('x=1')
    a = agent(tmp_path)
    step = {'id':'inspect','title':'Inspect code','acceptance':'Source inspected','status':'completed','evidence':[]}
    assert a.execute_tool('update_plan', {'steps':json.dumps([step])}).metadata['tool_status'] == 'error'
    read = a.execute_tool('read_file', {'path':'a.py'})
    step['evidence'] = [read.metadata['evidence_id']]
    assert a.execute_tool('update_plan', {'steps':json.dumps([step])}).metadata['tool_status'] == 'ok'
    data = a.session_store.load(a.session['id']); data['plan'] = []; a.session_store.save(data)
    restored = Pico.from_session(FakeModelClient([]), WorkspaceContext.build(tmp_path), a.session_store,
                                 a.session['id'], harness_profile='workbench')
    assert restored.session['plan'][0]['status'] == 'completed'


def test_skill_is_loaded_only_on_demand_and_mcp_uses_real_child_protocol(tmp_path):
    skills = tmp_path/'.codetrail/skills/repair'; skills.mkdir(parents=True)
    (skills/'SKILL.md').write_text('---\nname: repair\ndescription: Verify repairs\n---\nRead tests then repair. Never grants permission.')
    (tmp_path/'README.md').write_text('Run unittest to verify repairs.\n')
    a = agent(tmp_path)
    assert a.capabilities.skills[0]['name'] == 'repair'
    assert 'Read tests then repair.' in a.execute_tool('load_skill', {'name':'repair'}).content
    a.approve = lambda *args: False
    assert a.execute_tool('mcp_docs', {'query':'unittest'}).metadata['tool_error_code'] == 'approval_denied'
    a.approve = lambda *args: True
    result = a.execute_tool('mcp_docs', {'query':'unittest'})
    assert result.metadata['tool_status'] == 'ok' and 'README.md:1:' in result.content


def call(name, args, key):
    return {'id':key,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}


def test_native_batch_executes_ordered_with_actual_ids_and_individual_approvals(tmp_path):
    (tmp_path/'a.py').write_text('x=1\n')
    client = OpenAICompatibleModelClient('deepseek-ai/DeepSeek-V4.1-Flash','https://api.siliconflow.cn/v1','unit-test-key',.2,10,'chat_completions')
    a = agent(tmp_path); a.model_client = client
    approvals = []; a.approve = lambda name,args: approvals.append(name) or True
    outputs = [
        {'choices':[{'message':{'content':None,'tool_calls':[call('read_file',{'path':'a.py'},'read'),call('patch_file',{'path':'a.py','old_text':'x=1','new_text':'x=2'},'edit')]},'finish_reason':'tool_calls'}]},
        {'choices':[{'message':{'content':'Changed x.'},'finish_reason':'stop'}]}
    ]
    class Response:
        headers = {'Content-Type':'application/json'}
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return json.dumps(outputs.pop(0)).encode()
    with patch('urllib.request.urlopen', return_value=Response()):
        assert a.ask('Change x') == 'Changed x.'
    assert approvals == ['patch_file']
    assert [m['tool_call_id'] for m in client.native_conversation.messages if m['role']=='tool'] == ['read','edit']
    assert (tmp_path/'a.py').read_text() == 'x=2\n'


def test_batch_exhaustion_closes_ids_without_extra_effects(tmp_path):
    from pico.providers.native_tools import NativeReply
    a = agent(tmp_path, max_steps=1)
    class BatchClient:
        supports_native_tools=True
        last_completion_metadata={}
        def begin_native_run(self, tools,*args):
            self.native_conversation = NativeToolConversation(tools,'ctx','task',None,require_reasoning=False)
        def complete(self,*args,**kwargs):
            if self.native_conversation.messages[-1].get('role')=='user' and 'exhausted' in self.native_conversation.messages[-1].get('content',''):
                return NativeReply('Budget ended','final')
            return self.native_conversation.accept({'choices':[{'message':{'tool_calls':[call('write_file',{'path':'a','content':'a'},'a'),call('write_file',{'path':'b','content':'b'},'b')]}}]}, {})
        def observe_native_tool(self,text): self.native_conversation.observe(text)
        def native_notice(self,text): self.native_conversation.notice(text)
    a.model_client=BatchClient()
    a.ask('write')
    assert (tmp_path/'a').exists() and not (tmp_path/'b').exists()
    assert a.model_client.native_conversation.pending is None


def test_stream_assembles_split_arguments_and_never_exposes_reasoning():
    events = [
        {'choices':[{'delta':{'reasoning_content':'private reason'}}]},
        {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'a','type':'function','function':{'name':'read_file','arguments':'{"pa'}}]}}]},
        {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'th":"a.py"}'}}]},'finish_reason':'tool_calls'}]},
        {'choices':[], 'usage':{'prompt_tokens':9,'completion_tokens':7}}
    ]
    raw = b''.join(('data: '+json.dumps(e)+'\n\n').encode() for e in events) + b'data: [DONE]\n\n'
    visible=[]; data=read_chat_stream(io.BytesIO(raw),on_delta=visible.append)
    assert data['choices'][0]['message']['tool_calls'][0]['function']['arguments']=='{"path":"a.py"}'
    assert data['usage']['prompt_tokens']==9 and not visible
    with pytest.raises(ValueError,match='Incomplete'):
        read_chat_stream(io.BytesIO(raw.split(b'data: [DONE]')[0]))


def test_cancel_kills_host_child_and_journals_uncertain(tmp_path):
    a = agent(tmp_path)
    result=[]
    command = f'"{sys.executable}" -c "import time; time.sleep(20)"'
    thread=threading.Thread(target=lambda: result.append(a.execute_tool('run_shell',{'command':command,'timeout':30})))
    thread.start()
    deadline=time.monotonic()+10
    while not a.operations.unresolved() and time.monotonic()<deadline: time.sleep(.02)
    time.sleep(.2); a.cancel(); thread.join(10)
    assert not thread.is_alive()
    assert result[0].metadata['tool_error_code']=='cancelled'
    assert a.operations.unresolved()[0]['status']=='uncertain'


def test_unknown_usage_is_not_zero():
    from pico.web_status import usage_summary
    result=usage_summary([{'event':'model_requested'},{'event':'model_parsed','completion_metadata':{'input_tokens':10,'output_tokens':None}}])
    assert result['input_tokens']==10 and result['output_tokens'] is None and not result['complete']
    assert result['known_output_tokens']==0 and result['unknown_output_tokens_requests']==1


def test_policy_resolves_aliases_and_rechecks_after_approval(tmp_path):
    (tmp_path/'a.py').write_text('x=1')
    a=agent(tmp_path)
    policy=tmp_path/'.pico/policy.json'
    policy.write_text(json.dumps({'protected_paths':['a.py']}))
    result=a.execute_tool('patch_file',{'path':'./a.py','old_text':'x=1','new_text':'x=2'})
    assert result.metadata['tool_error_code']=='policy_hook_denied'
    policy.write_text('{}')
    def approve(*values):
        policy.write_text(json.dumps({'deny_tools':['patch_file']}));return True
    a.approve=approve
    result=a.execute_tool('patch_file',{'path':'a.py','old_text':'x=1','new_text':'x=2'})
    assert result.metadata['tool_error_code']=='policy_hook_denied' and (tmp_path/'a.py').read_text()=='x=1'
    assert not a.operations.unresolved()


def test_evidence_identity_binds_parameters_and_snapshot(tmp_path):
    a=agent(tmp_path)
    first=a.evidence.put(a.session['id'],'run','run_shell','exit_code: 0',{'args':{'command':'first'},'snapshot_sha256':'a'})
    second=a.evidence.put(a.session['id'],'run','run_shell','exit_code: 0',{'args':{'command':'second'},'snapshot_sha256':'a'})
    third=a.evidence.put(a.session['id'],'run','run_shell','exit_code: 0',{'args':{'command':'first'},'snapshot_sha256':'b'})
    assert len({first,second,third})==3


def test_cancel_after_approval_does_not_start_effect(tmp_path):
    from pico.control import Cancelled
    a=agent(tmp_path)
    def approve(*values):a.cancel();return True
    a.approve=approve
    with pytest.raises(Cancelled):a.execute_tool('write_file',{'path':'blocked','content':'no'})
    assert not (tmp_path/'blocked').exists() and not a.operations.unresolved()


def test_native_parameter_types_and_hardlink_boundary(tmp_path):
    import os
    a=agent(tmp_path)
    (tmp_path/'a.py').write_text('x=1')
    assert a.execute_tool('read_file',{'path':'a.py','start':True}).metadata['tool_status']=='rejected'
    assert a.execute_tool('read_file',{'path':'a.py','unexpected':'value'}).metadata['tool_status']=='rejected'
    os.link(tmp_path/'a.py',tmp_path/'linked.py')
    assert a.execute_tool('read_file',{'path':'linked.py'}).metadata['tool_status']=='rejected'
