import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from pico.providers.clients import FakeModelClient
from pico.providers.native_tools import NativeToolConversation
from pico.runtime import Pico,SessionStore
from pico.workspace import WorkspaceContext
from pico.windows_sandbox import snapshot,VERIFY,run_verification,SandboxUnavailable
from pico.tools import BASE_TOOL_SPECS


def test_native_budget_is_single_runtime_frame_and_reserves_last_step():
    s=NativeToolConversation(BASE_TOOL_SPECS,'workspace','repair',VERIFY)
    s.set_budget(3);s.set_budget(2);s.set_budget(1)
    assert len([m for m in s.messages if m.get('content','').startswith('Runtime budget:')])==1
    names={t['function']['name'] for t in s.payload()['tools']}
    assert 'run_shell' in names and not names & {'write_file','patch_file'}
    assert s.audit_request()['tools']==s.payload()['tools']


def test_native_schema_exposes_real_timeout_and_exact_isolated_command():
    specs={k:dict(v) for k,v in BASE_TOOL_SPECS.items()};specs['run_shell']['command_allowlist']=[VERIFY]
    s=NativeToolConversation(specs,'workspace','repair',VERIFY)
    properties=next(t['function']['parameters']['properties'] for t in s.tools if t['function']['name']=='run_shell')
    assert properties['timeout']['minimum']==1 and properties['timeout']['maximum']==120
    assert properties['command']['enum']==[VERIFY]


def test_last_step_write_is_rejected_on_server_even_if_model_ignores_schema(tmp_path):
    (tmp_path/'app.py').write_text('original')
    model=FakeModelClient([]);model.supports_native_tools=True
    agent=Pico(model,WorkspaceContext.build(tmp_path,repo_root_override=tmp_path),SessionStore(tmp_path/'.pico/sessions'),approval_policy='auto',verification_command=VERIFY)
    agent.remaining_tool_steps=1
    result=agent.execute_tool('write_file',{'path':'app.py','content':'unverified'})
    assert result.metadata['tool_error_code']=='verification_budget_reserved'
    assert (tmp_path/'app.py').read_text()=='original'


def test_appcontainer_configuration_cannot_be_used_for_arbitrary_shell(tmp_path):
    with pytest.raises(ValueError):
        Pico(FakeModelClient([]),WorkspaceContext.build(tmp_path,repo_root_override=tmp_path),SessionStore(tmp_path/'.pico/sessions'),verification_command='whoami',shell_backend='appcontainer')
    with pytest.raises(ValueError):run_verification(tmp_path,'whoami')


def test_appcontainer_failure_never_falls_back_to_host(tmp_path):
    with patch('pico.windows_sandbox._execute',side_effect=SandboxUnavailable('disabled')),patch('subprocess.run') as host:
        with pytest.raises(SandboxUnavailable):run_verification(tmp_path,VERIFY)
    host.assert_not_called()
    audit=json.loads(next((tmp_path/'.pico/sandbox').glob('*/audit.json')).read_text())
    assert audit['host_fallback'] is False and audit['status']=='failed_closed'


def test_snapshot_excludes_secrets_and_runtime_state(tmp_path):
    src=tmp_path/'src';src.mkdir();dst=tmp_path/'copy'
    for name in ('.env','known.ENV','app.py'):(src/name).write_text('public fixture')
    (src/'.pico').mkdir();(src/'.pico/token').write_text('private fixture')
    snapshot(src,dst)
    assert sorted(p.relative_to(dst).as_posix() for p in dst.rglob('*') if p.is_file())==['app.py']


def test_snapshot_refuses_known_secret_in_source(tmp_path,monkeypatch):
    src=tmp_path/'src';src.mkdir();(src/'app.py').write_text('PUBLIC_UNIT_ONLY_SECRET_123')
    monkeypatch.setenv('UNIT_API_KEY','PUBLIC_UNIT_ONLY_SECRET_123')
    with pytest.raises(ValueError,match='Known credential'):snapshot(src,tmp_path/'copy')


def test_snapshot_refuses_file_link(tmp_path):
    src=tmp_path/'src';src.mkdir();(tmp_path/'outside').write_text('canary')
    try:(src/'link.py').symlink_to(tmp_path/'outside')
    except OSError:pytest.skip('No symlink support')
    with pytest.raises(ValueError,match='Reparse'):snapshot(src,tmp_path/'copy')


def test_snapshot_refuses_hardlink(tmp_path):
    src=tmp_path/'src';src.mkdir();target=tmp_path/'outside';target.write_text('canary')
    try:os.link(target,src/'app.py')
    except OSError:pytest.skip('No hardlink support')
    with pytest.raises(ValueError,match='hardlinked'):snapshot(src,tmp_path/'copy')


@pytest.mark.skipif(os.name!='nt',reason='Requires actual Windows AppContainer')
def test_actual_appcontainer_runtime_checks_host_boundary_and_memory_limit(tmp_path):
    host=tmp_path/'outside';host.write_text('public canary');workspace=tmp_path/'workspace';workspace.mkdir()
    code='''import unittest,os
from pathlib import Path
class Tests(unittest.TestCase):
 def test_host_read(self):
  with self.assertRaises(PermissionError):Path(HOST).read_text()
 def test_host_write(self):
  with self.assertRaises(PermissionError):Path(HOST).write_text('changed')
 def test_memory(self):
  with self.assertRaises(MemoryError):bytearray(300*1024*1024)
 def test_env(self):self.assertNotIn('SILICONFLOW_API_KEY',os.environ)
 def test_positive(self):self.assertEqual(2+3,5)
'''.replace('HOST',repr(str(host)))
    (workspace/'test_acceptance.py').write_text(code)
    agent=Pico(FakeModelClient([]),WorkspaceContext.build(workspace,repo_root_override=workspace),SessionStore(workspace/'.pico/sessions'),verification_command=VERIFY,shell_backend='appcontainer')
    agent.approve=lambda name,args:name=='run_shell' and args.get('command')==VERIFY
    result=agent.execute_tool('run_shell',{'command':VERIFY,'timeout':20})
    assert result.metadata['exit_code']==0,result.content
    assert result.metadata['execution_backend']=='appcontainer'
    assert 'Ran 5 tests' in result.content and host.read_text()=='public canary'
    audit=json.loads(next((workspace/'.pico/sandbox').glob('*/audit.json')).read_text())
    assert audit['kernel_token_is_appcontainer'] and not audit['host_acl_changed']


@pytest.mark.skipif(os.name!='nt',reason='Requires actual Windows AppContainer')
def test_actual_appcontainer_output_flood_is_terminated(tmp_path):
    (tmp_path/'test_acceptance.py').write_text('import unittest\nclass Tests(unittest.TestCase):\n def test_print(self):print("x"*1000000)\n')
    with pytest.raises(SandboxUnavailable,match='output exceeded'):run_verification(tmp_path,VERIFY,10)


@pytest.mark.parametrize('status',[401,402,403])
def test_terminal_account_errors_stop_new_work(status):
    from scripts.run_final_agent_eval import fatal_provider_reason
    assert fatal_provider_reason({'error':f'RuntimeError: request failed with HTTP {status}'})
    assert fatal_provider_reason({'error':'request failed with HTTP 429'}) is None


def test_dispatcher_stops_after_balance_failure_and_retains_unstarted_slots(tmp_path,monkeypatch):
    import scripts.run_final_agent_eval as r
    plan=[{'run_key':str(i),'task_id':'scripted','repeat':i} for i in range(90)]
    r.write_json(tmp_path/'plan.json',plan);launches=[]
    monkeypatch.setattr(r,'check',lambda _:None);monkeypatch.setattr(r,'client',lambda:None)
    monkeypatch.setattr(r,'CONFIG',{**r.CONFIG,'concurrency':1})
    monkeypatch.setattr(r,'aggregate_metrics',lambda rows:{'retained':len(rows)})
    class Completed:
        def __init__(self,args,**kwargs):
            key=args[-1];launches.append(key);d=tmp_path/'runs'/key;d.mkdir(parents=True)
            r.write_json(d/'result.json',{'run_key':key,'functional_passed':False,'workflow_passed':False,
                'failure_category':'provider_or_runtime_error','elapsed_ms':1,'error':'HTTP 402 code30001 balance insufficient'})
        def poll(self):return 0
    monkeypatch.setattr(r.subprocess,'Popen',Completed)
    r.run(tmp_path)
    result=r.load_json(tmp_path/'results.json');stopped=r.load_json(tmp_path/'stopped.json')
    assert launches==['0'] and len(result['rows'])==1 and len(result['not_started'])==89
    assert not result['completed'] and stopped['reason']=='api_billing_insufficient_balance'


def test_historical_evidence_and_gold_remain_exact():
    root=Path(__file__).resolve().parents[1];public=root/'benchmarks/results/final-agent-90-2026-10-25'
    assert hashlib.sha256((public/'evidence.zip').read_bytes()).hexdigest()=='2fc5e42cf764504137d4243b509171bcba46d3df6a254896e827ed42487d1c76'
    report=json.loads((public/'audit.json').read_text())
    assert hashlib.sha256((root/'benchmarks/final_agent_v1/evidence_gold.json').read_bytes()).hexdigest()==report['gold_sha256']
