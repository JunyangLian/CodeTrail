"""Separate official DeepSeek validation, reusing immutable v1 tasks and verifier."""
import argparse
import difflib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pico.cli import build_arg_parser,_build_model_client,_configured_secret_names
from pico.config import _parse_env_line
from pico.evaluation.baseline_assets import verify_baseline_assets
from pico.evaluation.repair_suite import (SUITE,VERIFY_COMMAND,RecordingClient,aggregate,
    append_json,digest,install,inventory,load_json,trace_metrics,utcnow,verify_pristine,write_json)
from pico.runtime import Pico,SessionStore,DEFAULT_SHELL_ENV_ALLOWLIST
from pico.workspace import WorkspaceContext

DEFAULT_OUTPUT='artifacts/deepseek-validation-thinking-2026-10-25'
TASK_IDS=['inventory_reservation','utc_storage_contract','boltons_splitlines','boltons_xfrange','humanize_metric','humanize_size']
CONFIG={'model':'deepseek-flash','provider':'openai','api_format':'chat_completions','base_url':'https://api.deepseek.com/v1',
    'response_protocol':'One complete official DeepSeek DSML call normalized to the existing tool envelope; all tool checks and approval retained.',
    'thinking':'Official default enabled/high; temperature is ignored in thinking mode','temperature':.2,'request_timeout_seconds':180,
    'max_steps':20,'max_new_tokens':8192,'repetitions':2,'concurrency':3,'run_deadline_seconds':900,
    'verify_command':VERIFY_COMMAND,'scope':'Separate six known tasks x two runs; not a replacement of earlier results.',
    'comparison_boundary':'Provider, model, protocol and thinking mode differ from SiliconFlow/GLM. Not a provider-only causal comparison.'}


def credentials():
    path=ROOT/'.env.deepseek.local'
    values=dict(p for p in (_parse_env_line(l) for l in path.read_text(encoding='utf-8').splitlines()) if p)
    assert values['PICO_DEEPSEEK_API_BASE']=='https://api.deepseek.com/anthropic'
    assert values['PICO_DEEPSEEK_MODEL']=='deepseek-flash'
    os.environ.update(values)
    return values


class AuditedClient:
    """Observe actual HTTP attempts without modifying the frozen provider client."""
    def __init__(self,client,directory):
        self.client=client;self.directory=directory
        self.model=client.model;self.base_url=client.base_url;self.thinking=client.thinking
        self.supports_prompt_cache=False;self.last_completion_metadata={};self.on_transport_event=None

    def complete(self,*args,**kwargs):
        original=urllib.request.urlopen;events=[]
        owner=self
        def record(started,ok,**values):
            event={'attempt':len(events)+1,'ok':ok,'at':utcnow(),
                'elapsed_ms':round((time.monotonic()-started)*1000),**values}
            events.append(event);append_json(owner.directory/'transport.jsonl',event)
            if owner.on_transport_event:
                owner.on_transport_event(event)
        class Response:
            def __init__(self,response,started):self.response=response;self.started=started
            def __getattr__(self,name):return getattr(self.response,name)
            def __enter__(self):self.response.__enter__();return self
            def __exit__(self,*exc):return self.response.__exit__(*exc)
            def read(self,*a,**k):
                try:
                    data=self.response.read(*a,**k)
                    headers=self.response.headers
                    request_id=next((headers.get(name) for name in ('x-request-id','x-ds-request-id','x-ds-trace-id') if headers.get(name)),None)
                    record(self.started,True,status_code=self.response.status,request_id=request_id)
                    return data
                except Exception as exc:
                    record(self.started,False,error_type=type(exc).__name__)
                    raise
        def urlopen(request,*a,**k):
            parsed=urllib.parse.urlparse(request.full_url)
            assert parsed.scheme=='https' and parsed.hostname=='api.deepseek.com' and parsed.path=='/anthropic/v1/messages'
            started=time.monotonic()
            try:return Response(original(request,*a,**k),started)
            except Exception as exc:
                record(started,False,error_type=type(exc).__name__,status_code=getattr(exc,'code',None))
                raise
        urllib.request.urlopen=urlopen
        try:return self.client.complete(*args,**kwargs)
        finally:
            urllib.request.urlopen=original
            failures=sum(not e['ok'] for e in events)
            self.last_completion_metadata={**self.client.last_completion_metadata,
                'transport_attempts':len(events),'transport_failures':failures,'transport_usage_complete':not failures,
                'unknown_usage_attempts':failures,'transport_events':events}


def freeze(output):
    baseline=verify_baseline_assets()
    output.mkdir(parents=True,exist_ok=False)
    plan=[{'task_id':task,'repeat':repeat} for repeat in (1,2) for task in TASK_IDS]
    random.Random(20261025).shuffle(plan)
    for index,row in enumerate(plan,1):row['run_key']=f"{index:02d}-{row['task_id']}-r{row['repeat']}"
    write_json(output/'plan.json',plan,exclusive=True)
    write_json(output/'protocol.json',CONFIG,exclusive=True)
    files=list((ROOT/'pico').rglob('*.py'))+[Path(__file__).resolve(),ROOT/'scripts/repair_external_verifier.py',SUITE/'tasks.json']
    write_json(output/'freeze.json',{'at':utcnow(),'configuration':CONFIG,'baseline_assets':baseline,
        'source_hashes':{p.relative_to(ROOT).as_posix():digest(p) for p in files if '__pycache__' not in p.parts},
        'plan_sha256':digest(output/'plan.json'),'protocol_sha256':digest(output/'protocol.json')},exclusive=True)
    print(json.dumps({'frozen':12,'sha256':digest(output/'freeze.json')}),flush=True)


def check(output):
    data=load_json(output/'freeze.json')
    assert data['configuration']==CONFIG
    for name,expected in data['source_hashes'].items():assert digest(ROOT/name)==expected,name
    assert digest(output/'plan.json')==data['plan_sha256']
    assert digest(output/'protocol.json')==data['protocol_sha256']


def worker(output,key):
    check(output);private=credentials()
    os.environ['PICO_OPENAI_API_KEY']=private['PICO_DEEPSEEK_API_KEY']
    slot=next(r for r in load_json(output/'plan.json') if r['run_key']==key)
    task=next(t for t in load_json(SUITE/'tasks.json')['tasks'] if t['id']==slot['task_id'])
    directory=output/'runs'/key;directory.mkdir(parents=True,exist_ok=False)
    workspace=directory/'workspace';install(workspace,task['files']);before=inventory(workspace)
    assert before==task['sha256']
    os.environ.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    args=build_arg_parser().parse_args(['--provider','openai','--api-format','chat_completions','--model','deepseek-flash',
        '--base-url',CONFIG['base_url'],'--openai-timeout','180'])
    row={**slot,'category':task['category'],'origin_kind':task['origin']['kind'],'started_at':utcnow(),
        'initial_sha256':before,'approvals':[],'workspace':workspace.relative_to(ROOT).as_posix()}
    start=time.monotonic();agent=None;events=[]
    try:
        raw=_build_model_client(args)
        assert raw.base_url=='https://api.deepseek.com/v1' and raw.api_format=='chat_completions'
        model=raw
        model.on_transport_event=lambda event:append_json(directory/'transport.jsonl',event)
        # Forward per-attempt observations through the existing runtime hook.
        class Recorder(RecordingClient):
            @property
            def on_transport_event(self):return self.client.on_transport_event
            @on_transport_event.setter
            def on_transport_event(self,value):self.client.on_transport_event=value
        recorder=Recorder(model,directory/'model_io.jsonl');recorder.model=model.model;recorder.base_url=model.base_url
        context=WorkspaceContext(str(workspace),str(workspace),'-','-','fresh fixture',[],{'README.md':task['files']['README.md']})
        agent=Pico(recorder,context,SessionStore(workspace/'.pico/sessions'),approval_policy='ask',
            max_steps=20,max_new_tokens=CONFIG['max_new_tokens'],max_depth=0,verification_command=VERIFY_COMMAND,
            allowed_tools=['list_files','read_file','search','patch_file','write_file','run_shell'],
            shell_env_allowlist=DEFAULT_SHELL_ENV_ALLOWLIST+('PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING'),
            secret_env_names=_configured_secret_names(args))
        assert not agent.session['history'];row['session_id']=agent.session['id']
        def approve(name,arguments):
            allowed=False
            if name=='run_shell':allowed=arguments.get('command','').strip()==VERIFY_COMMAND
            elif name in {'patch_file','write_file'}:
                try:allowed=agent.path(arguments.get('path','')).relative_to(workspace).as_posix() in task['editable_files']
                except (TypeError,ValueError):pass
            event={'name':name,'args':arguments,'allowed':allowed,'at':utcnow()}
            row['approvals'].append(event);append_json(directory/'approvals.jsonl',event)
            return allowed
        agent.approve=approve
        for name,tool in agent.tools.items():
            original=tool['run']
            def recording(arguments,name=name,original=original):
                value=original(arguments)
                append_json(directory/'tool_io.jsonl',{'name':name,'args':arguments,'output':str(value),'at':utcnow()})
                return value
            tool['run']=recording
        row['answer']=agent.ask(task['prompt'])
    except BaseException as exc:row['error']=f'{type(exc).__name__}: {exc}'
    row['agent_elapsed_ms']=round((time.monotonic()-start)*1000)
    after=inventory(workspace);row['final_sha256']=after
    changed=[n for n in sorted(before.keys()|after.keys()) if before.get(n)!=after.get(n)]
    row['changed_files']=changed;row['scope_valid']=set(changed)<=set(task['editable_files'])
    row['protected_files_unchanged']=all(after.get(n)==v for n,v in before.items() if n not in task['editable_files'])
    patches=[]
    for name in changed:
        path=workspace/name
        patches.extend(difflib.unified_diff(task['files'].get(name,'').splitlines(True),
            path.read_text(encoding='utf-8',errors='replace').splitlines(True) if path.is_file() else [],fromfile='a/'+name,tofile='b/'+name))
    (directory/'changes.diff').write_text(''.join(patches),encoding='utf-8',newline='\n')
    implementation={n:(workspace/n).read_bytes() for n in task['editable_files'] if (workspace/n).is_file()}
    row['external_verifier']=verify_pristine(task,implementation,directory/'external_final')
    row['functional_passed']=bool(row['scope_valid'] and row['protected_files_unchanged'] and len(implementation)==len(task['editable_files']) and row['external_verifier']['accepted'])
    state=getattr(agent,'current_task_state',None)
    if state:
        row.update(run_id=state.run_id,status=state.status,stop_reason=state.stop_reason,attempts=state.attempts)
        trace=agent.run_store.trace_path(state);events=[json.loads(l) for l in trace.read_text(encoding='utf-8').splitlines()]
        row.update(trace=trace.relative_to(ROOT).as_posix(),trace_sha256=digest(trace),completion_verification=dict(agent.last_verification))
    row.update(trace_metrics(events,after,before))
    transport=[json.loads(l) for l in (directory/'transport.jsonl').read_text(encoding='utf-8').splitlines()] if (directory/'transport.jsonl').exists() else []
    row['transport_attempts']=len(transport);row['transport_failures']=sum(not e['ok'] for e in transport)
    for metric in ('input_tokens','output_tokens'):row[metric]['complete']=row[metric]['complete'] and not row['transport_failures'] and not row.get('error')
    row['workflow_passed']=bool(row['functional_passed'] and row.get('status')=='completed' and row.get('agent_verified_after_last_write') and not row.get('error'))
    row['failure_category']='success' if row['workflow_passed'] else 'model_or_runtime_error' if row.get('error') else 'test_regression_or_incomplete_fix' if not row['functional_passed'] else 'incomplete_workflow'
    row.update(elapsed_ms=round((time.monotonic()-start)*1000),finished_at=utcnow())
    check(output);write_json(directory/'result.json',row,exclusive=True)
    print(json.dumps({k:row[k] for k in ('run_key','functional_passed','workflow_passed','failure_category','elapsed_ms')}),flush=True)


def run(output):
    check(output);write_json(output/'started.json',{'at':utcnow()},exclusive=True)
    plan=load_json(output/'plan.json');pending=list(plan);active={};(output/'logs').mkdir()
    while pending or active:
        while pending and len(active)<3:
            slot=pending.pop(0);key=slot['run_key'];log=(output/'logs'/(key+'.log')).open('xb')
            env=dict(os.environ,GIT_CEILING_DIRECTORIES=str(ROOT/'artifacts'),PYTHONDONTWRITEBYTECODE='1')
            process=subprocess.Popen([sys.executable,'-u','-X','utf8',str(Path(__file__).resolve()),'--output',str(output),'--worker',key],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            active[key]=(process,log,time.monotonic(),slot);print('START '+key,flush=True)
        for key,(process,log,start,slot) in list(active.items()):
            timeout=time.monotonic()-start>900
            if process.poll() is None and not timeout:continue
            if process.poll() is None:process.terminate();process.wait(timeout=15)
            log.close();directory=output/'runs'/key;directory.mkdir(exist_ok=True,parents=True)
            if not (directory/'result.json').exists():
                write_json(directory/'result.json',{**slot,'functional_passed':False,'workflow_passed':False,
                    'failure_category':'run_deadline' if timeout else 'worker_error','error':'Worker stopped; no replacement',
                    'elapsed_ms':round((time.monotonic()-start)*1000)},exclusive=True)
            row=load_json(directory/'result.json');print(json.dumps({k:row.get(k) for k in ('run_key','workflow_passed','error','elapsed_ms')}),flush=True)
            del active[key]
            write_json(output/'progress.json',{'completed':len(list((output/'runs').glob('*/result.json'))),'planned':12})
        if active:time.sleep(.2)
    rows=[load_json(output/'runs'/r['run_key']/'result.json') for r in plan]
    check(output);write_json(output/'results.json',{'completed':True,'at':utcnow(),'configuration':CONFIG,'rows':rows,'summary':aggregate(rows)},exclusive=True)
    print(json.dumps(aggregate(rows)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default=DEFAULT_OUTPUT)
    parser.add_argument('--freeze',action='store_true');parser.add_argument('--run',action='store_true');parser.add_argument('--worker')
    args=parser.parse_args();output=(ROOT/args.output).resolve()
    if ROOT not in output.parents:parser.error('Output must stay inside project')
    if args.freeze:freeze(output)
    elif args.worker:worker(output,args.worker)
    elif args.run:run(output)
    else:parser.print_help()
