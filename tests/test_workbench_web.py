import json
from pathlib import Path
import threading
import time

import pytest

from pico.providers.clients import FakeModelClient
from pico.runtime import Pico, SessionStore
from pico.web import Workbench, RequestError
from pico.workspace import WorkspaceContext


def app(root, outputs=()):
    def model():
        value = FakeModelClient(list(outputs)); value.model = 'scripted-test-fixture'; return value
    def factory(session_id, policy, steps):
        options = dict(model_client=model(), workspace=WorkspaceContext.build(root), session_store=SessionStore(root/'.pico/sessions'),
                       approval_policy=policy, read_only=policy=='never', max_steps=steps, harness_profile='workbench', max_depth=0)
        return Pico.from_session(session_id=session_id, **options) if session_id else Pico(**options)
    return Workbench(root, factory=factory, model_factory=model, harness_profile='workbench')


def finish(task):
    with task.condition:
        assert task.condition.wait_for(lambda: task.finished is not None, timeout=15)
    return task.view()


def test_web_cancels_waiting_approval_and_preserves_restart_view(tmp_path):
    workbench = app(tmp_path, ['<tool>{"name":"write_file","args":{"path":"blocked.txt","content":"no"}}</tool>', '<final>Done</final>'])
    task = workbench.task(workbench.submit({'prompt':'write with approval'})['id'])
    with task.condition:
        assert task.condition.wait_for(lambda: task.approval is not None, timeout=10)
        old = task.approval['id']
    workbench.cancel(task.id)
    result = finish(task)
    assert result['status']=='stopped' and result['stop_reason']=='cancelled'
    assert not (tmp_path/'blocked.txt').exists()
    restored = app(tmp_path).task(task.id).view()
    assert restored['status']=='stopped' and restored['approval'] is None
    with pytest.raises(RequestError): workbench.approve(task.id, old, True)


def test_restart_invalidates_old_approval_and_resumes_in_new_task(tmp_path):
    workbench=app(tmp_path,['<final>inspected</final>'])
    task=workbench.task(workbench.submit({'prompt':'inspect','policy':'never'})['id']); finish(task)
    path=tmp_path/'.pico/web/tasks'/f'{task.id}.json'
    saved=json.loads(path.read_text()); saved.update(status='waiting_approval',approval={'id':'expired','name':'write_file'})
    path.write_text(json.dumps(saved))
    restarted=app(tmp_path,['<final>new inspection</final>'])
    recovered=restarted.task(task.id)
    assert recovered.status=='stopped' and recovered.stop_reason=='server_restarted' and recovered.approval is None
    next_task=restarted.task(restarted.resume(task.id)['id'])
    assert next_task.id!=task.id and next_task.session_id==task.session_id
    assert finish(next_task)['status']=='completed'


def test_governance_reads_do_not_overwrite_live_session_and_confirmation_uses_cas(tmp_path):
    workbench=app(tmp_path,['<final>hello</final>'])
    task=workbench.task(workbench.submit({'prompt':'inspect'})['id']); result=finish(task)
    session=tmp_path/'.pico/sessions'/f"{result['session_id']}.json"
    before=session.read_bytes()
    a=workbench.control_agent(result['session_id'])
    (tmp_path/'a.py').write_text('x=1')
    memory=a.memory.knowledge.propose('Project x is one','fact:x',['a.py'])
    view=workbench.governance(result['session_id'])
    assert session.read_bytes()==before and view['memories'][0]['status']=='candidate'
    view=workbench.govern({'action':'confirm_memory','session_id':result['session_id'],'id':memory['id'],'version':1})
    assert view['memories'][0]['status']=='active'
    with pytest.raises(ValueError,match='conflict'):
        workbench.govern({'action':'retract_memory','session_id':result['session_id'],'id':memory['id'],'version':1})
    (tmp_path/'a.py').write_text('x=2')
    assert workbench.governance(result['session_id'])['memories'][0]['status']=='stale'


def test_operation_reconciliation_requires_current_hash_and_reason(tmp_path):
    workbench=app(tmp_path,['<final>hello</final>'])
    task=workbench.task(workbench.submit({'prompt':'inspect'})['id']); result=finish(task)
    a=workbench.control_agent(result['session_id'])
    key,_=a.operations.begin('write_file',{'path':'a','content':'x'},a.capture_workspace_snapshot(),a.session['id'],'run')
    view=workbench.governance(result['session_id'])
    with pytest.raises(ValueError):
        workbench.govern({'action':'reconcile_operation','session_id':result['session_id'],'id':key,'snapshot_sha256':'wrong','reason':'checked'})
    done=workbench.govern({'action':'reconcile_operation','session_id':result['session_id'],'id':key,'snapshot_sha256':view['snapshot_sha256'],'reason':'Inspected files; no automatic replay'})
    assert done['operations']==[] and not (tmp_path/'a').exists()
