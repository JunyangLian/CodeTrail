import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from pico.completion import CompletionGate
from pico.providers.clients import FakeModelClient
from pico.providers.native_tools import NativeToolConversation
from pico.runtime import Pico, SessionStore
from pico.web import Workbench, Task
from pico.workspace import WorkspaceContext


def make(root, **kwargs):
    return Pico(FakeModelClient([]),WorkspaceContext.build(root,repo_root_override=root),SessionStore(root/'.pico/sessions'),
                harness_profile='workbench',approval_policy='auto',max_depth=0,**kwargs)


def test_protected_source_never_enters_recursive_tools_or_evidence(tmp_path):
    marker='PRIVATE_FIXTURE_CANARY_3491'
    (tmp_path/'confidential.py').write_text('def private_contract():\n return '+repr(marker)+'\n')
    (tmp_path/'public.py').write_text('def public_contract(): return 1\n')
    a=make(tmp_path)
    original=a.execute_tool('read_file',{'path':'confidential.py'})
    record=a.memory.knowledge.propose(marker,'fact:private',['confidential.py'])
    a.memory.knowledge.activate(record['id'],1,reviewer='fixture_operator')
    (tmp_path/'.pico/policy.json').write_text(json.dumps({'protected_paths':['confidential.py']}))
    for name,args in [('search',{'pattern':'PRIVATE|private_contract','path':'.'}),
                      ('code_search',{'query':'private_contract'}),('symbol_info',{'name':'private_contract'}),
                      ('find_evidence',{'query':marker}),('recall_evidence',{'evidence_id':original.metadata['evidence_id']})]:
        assert marker not in a.execute_tool(name,args).content
    assert not a.memory.retrieval_candidates(marker)
    assert marker not in a.memory.render_memory_text()


def test_document_mcp_respects_hardlink_and_current_policy(tmp_path):
    outside=tmp_path.parent/(tmp_path.name+'-canary.txt');outside.write_text('OUTSIDE_CANARY_456')
    docs=tmp_path/'docs';docs.mkdir();os.link(outside,docs/'leak.md')
    (tmp_path/'README.md').write_text('PROTECTED_DOCUMENT_123')
    a=make(tmp_path)
    (tmp_path/'.pico/policy.json').write_text(json.dumps({'protected_paths':['README.md']}))
    assert 'OUTSIDE_CANARY' not in a.execute_tool('mcp_docs',{'query':'OUTSIDE_CANARY'}).content
    assert 'PROTECTED_DOCUMENT' not in a.execute_tool('mcp_docs',{'query':'PROTECTED_DOCUMENT'}).content


def test_test_command_mutation_is_not_current_success(tmp_path):
    (tmp_path/'app.py').write_text('x=1')
    code="from pathlib import Path; p=Path('app.py'); assert p.read_text()=='x=1'; p.write_text('x=2')"
    command=subprocess.list2cmdline([sys.executable,'-c',code])
    a=make(tmp_path,verification_command=command)
    gate=CompletionGate(a)
    result=a.execute_tool('run_shell',{'command':command})
    gate.observe('run_shell',{'command':command},result.metadata)
    assert result.metadata['exit_code']==0 and result.metadata['workspace_changed']
    assert gate.state['status']=='stale' and gate.problem()
    steps=[{'id':'v','kind':'verification','title':'verify','acceptance':command,'status':'completed','evidence':[result.metadata['evidence_id']]}]
    assert a.execute_tool('update_plan',{'steps':json.dumps(steps)}).metadata['tool_status']=='error'


def test_crlf_retrieval_hash_can_be_used_for_edit(tmp_path):
    (tmp_path/'app.py').write_bytes(b'def compute():\r\n    return 1\r\n')
    a=make(tmp_path)
    row=a.capabilities.index.search('compute')[0]
    assert row['source_sha256']==hashlib.sha256((tmp_path/'app.py').read_bytes()).hexdigest()
    changes=[{'path':'app.py','expected_sha256':row['source_sha256'],'content':'def compute():\n    return 2\n'}]
    assert a.execute_tool('apply_changes',{'changes':json.dumps(changes)}).metadata['tool_status']=='ok'


def test_memory_ablation_applies_to_actual_native_request(tmp_path):
    class Fixture:
        supports_native_tools=True
        last_completion_metadata={}
        def begin_native_run(self,tools,context,request,verification_command):
            self.native_conversation=NativeToolConversation(tools,context,request,verification_command,require_reasoning=False)
        def complete(self,*args,**kwargs):
            self.payload=self.native_conversation.payload()
            return self.native_conversation.accept({'choices':[{'message':{'content':'inspected'}}]}, {})
    a=make(tmp_path,feature_flags={'memory':False,'relevant_memory':False})
    record=a.memory.knowledge.propose('UNIQUE_CONFIRMED_MEMORY_987','fact:unique')
    a.memory.knowledge.activate(record['id'],1,reviewer='fixture_operator')
    client=Fixture();a.model_client=client;a.ask('Inspect unique')
    assert 'UNIQUE_CONFIRMED_MEMORY_987' not in json.dumps(client.payload)
    assert not any('Current retrieved memory' in (m.get('content') or '') for m in client.payload['messages'])


def test_restart_discovers_interrupted_task_without_known_id(tmp_path):
    a=make(tmp_path)
    def model():
        x=FakeModelClient([]);x.model='offline-fixture';return x
    first=Workbench(tmp_path,model_factory=model,harness_profile='workbench')
    task=Task('interrupted',a.session['id'],'ask',10);task.status='waiting_approval';task.approval={'id':'old'}
    first._persist_task(task)
    restored=Workbench(tmp_path,model_factory=model,harness_profile='workbench')
    assert task.id in restored.config()['recoverable_tasks']
    assert any(r.get('task_id')==task.id for r in restored.runs())
    assert restored.task(task.id).view()['approval'] is None


def test_read_only_paths_are_readable_but_not_editable(tmp_path):
    (tmp_path/'test_contract.py').write_text('protected acceptance')
    a=make(tmp_path)
    (tmp_path/'.pico/policy.json').write_text(json.dumps({'read_only_paths':['test_contract.py']}))
    assert a.execute_tool('read_file',{'path':'test_contract.py'}).metadata['tool_status']=='ok'
    assert a.execute_tool('write_file',{'path':'./test_contract.py','content':'changed'}).metadata['tool_error_code']=='policy_hook_denied'


def test_nested_git_workspace_context_does_not_escape(tmp_path):
    subprocess.run(['git','init',str(tmp_path)],capture_output=True,check=True)
    child=tmp_path/'nested';child.mkdir()
    a=make(child);a.model_client=FakeModelClient(['<final>done</final>'])
    a.ask('Inspect')
    assert Path(a.workspace.repo_root)==child


def test_policy_revocation_stops_before_resending_context(tmp_path):
    (tmp_path/'app.py').write_text('PRIVATE_REVOKED_832')
    a=make(tmp_path);a.model_client=FakeModelClient(['<tool>{"name":"read_file","args":{"path":"app.py"}}</tool>','<final>must not be requested</final>'])
    original=a.tools['read_file']['run']
    def read(args):
        result=original(args)
        (tmp_path/'.pico/policy.json').write_text(json.dumps({'protected_paths':['app.py']}))
        return result
    a.tools['read_file']['run']=read
    a.ask('Inspect')
    assert a.current_task_state.stop_reason=='policy_changed' and len(a.model_client.prompts)==1


def test_sandbox_snapshot_uses_same_read_policy(tmp_path):
    from pico.windows_sandbox import snapshot
    source=tmp_path/'source';source.mkdir();(source/'public.py').write_text('x=1');(source/'private.py').write_text('private')
    a=make(source)
    (source/'.pico/policy.json').write_text(json.dumps({'protected_paths':['private.py']}))
    destination=tmp_path/'copy'
    snapshot(source,destination,path_filter=a.capabilities.allowed_path)
    assert (destination/'public.py').exists() and not (destination/'private.py').exists()


def test_memory_candidate_cannot_hash_forbidden_source(tmp_path):
    (tmp_path/'private.py').write_text('protected fixture')
    a=make(tmp_path)
    (tmp_path/'.pico/policy.json').write_text(json.dumps({'protected_paths':['private.py']}))
    result=a.execute_tool('memory_propose',{'text':'Candidate','key':'private','evidence_paths':json.dumps(['private.py'])})
    assert result.metadata['tool_status']=='error'
    with a.memory.knowledge.connect() as db:
        assert db.execute('SELECT count(*) FROM memories').fetchone()[0]==0


def test_disabled_memory_cannot_create_candidates(tmp_path):
    a=make(tmp_path,feature_flags={'memory':False})
    assert a.execute_tool('memory_propose',{'text':'Candidate','key':'disabled'}).metadata['tool_status']=='error'
