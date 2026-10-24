"""Separate post-fix regression: six previously observed problem tasks x two runs.

Reuses original immutable task/test assets. Never merges results into v1's 60.
"""
import argparse
from collections import Counter
import difflib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pico.evaluation.baseline_assets import verify_baseline_assets
from pico.evaluation.repair_suite import (SUITE,VERIFY_COMMAND,RecordingClient,aggregate,append_json,
    digest,install,inventory,load_json,trace_metrics,utcnow,verify_pristine,write_json)
from pico.cli import _build_model_client,_configured_secret_names,build_arg_parser
from pico.config import load_project_env
from pico.runtime import Pico,SessionStore,DEFAULT_SHELL_ENV_ALLOWLIST
from pico.workspace import WorkspaceContext

VERSION=ROOT/'benchmarks/repair_regression_v2'
TASK_IDS=['inventory_reservation','utc_storage_contract','boltons_splitlines','boltons_xfrange','humanize_metric','humanize_size']
CONFIG={'model':'zai-org/GLM-5.3-Flash','temperature':0.2,'max_steps':20,'max_new_tokens':3072,
        'request_timeout_seconds':180,'max_transport_attempts':3,'retry_time_budget_multiplier':2,
        'run_deadline_seconds':1200,'repetitions':2,'concurrency':3,'verify_command':VERIFY_COMMAND,
        'scope':'Post-hoc targeted regression on six known problem tasks; not a replacement or updated overall 20-task score.',
        'changes':'Strict response protocol + completion test gate + bounded timeout retries + per-attempt telemetry. Timeout180 vs v1 120; combined intervention, not an ablation.'}

class TransportRecordingClient(RecordingClient):
    @property
    def on_transport_event(self):return self.client.on_transport_event
    @on_transport_event.setter
    def on_transport_event(self,value):self.client.on_transport_event=value

def frozen():
    verify_baseline_assets()
    data=load_json(VERSION/'freeze.json')
    for relative,expected in data['hashes'].items():assert digest(ROOT/relative)==expected,relative
    assert data['configuration']==CONFIG
    return data

def freeze():
    baseline=verify_baseline_assets()
    VERSION.mkdir(parents=True,exist_ok=False)
    tasks={t['id']:t for t in load_json(SUITE/'tasks.json')['tasks']}
    assert set(TASK_IDS)<=tasks.keys()
    plan=[{'task_id':task,'repeat':repeat} for repeat in (1,2) for task in TASK_IDS]
    random.Random(20261025).shuffle(plan)
    for index,row in enumerate(plan,1):row['run_key']=f"{index:02d}-{row['task_id']}-r{row['repeat']}"
    write_json(VERSION/'plan.json',plan,exclusive=True)
    write_json(VERSION/'protocol.json',CONFIG,exclusive=True)
    files=list((ROOT/'pico').rglob('*.py'))+[Path(__file__).resolve(),ROOT/'scripts/repair_external_verifier.py',VERSION/'plan.json',VERSION/'protocol.json']
    hashes={p.relative_to(ROOT).as_posix():digest(p) for p in files if '__pycache__' not in p.parts}
    write_json(VERSION/'freeze.json',{'frozen_at':utcnow(),'configuration':CONFIG,'selected_tasks':TASK_IDS,
        'selection':'Chosen from known v1 failures before any v2 requests; includes three completion/contract cases and three all-timeout tasks.',
        'baseline':baseline,'task_manifest_sha256':digest(SUITE/'tasks.json'),'hashes':hashes},exclusive=True)
    print(json.dumps({'freeze_sha256':digest(VERSION/'freeze.json'),'planned_runs':12}),flush=True)

def execute(output,key):
    frozen();slot=next(r for r in load_json(VERSION/'plan.json') if r['run_key']==key)
    task=next(t for t in load_json(SUITE/'tasks.json')['tasks'] if t['id']==slot['task_id'])
    directory=output/'runs'/key;directory.mkdir(parents=True,exist_ok=False)
    write_json(directory/'started.json',{'at':utcnow(),**slot,'freeze_sha256':digest(VERSION/'freeze.json')},exclusive=True)
    workspace=directory/'workspace';install(workspace,task['files']);before=inventory(workspace)
    assert before==task['sha256']
    load_project_env(ROOT)
    os.environ['PYTHONDONTWRITEBYTECODE']='1';os.environ['PYTHONIOENCODING']='utf-8'
    args=build_arg_parser().parse_args(['--provider','openai','--model',CONFIG['model'],
        '--base-url','https://api.siliconflow.cn/v1','--api-format','chat_completions',
        '--temperature','.2','--openai-timeout','180'])
    row={**slot,'category':task['category'],'origin_kind':task['origin']['kind'],'started_at':utcnow(),
         'initial_sha256':before,'approvals':[],'functional_passed':False,'workflow_passed':False,
         'workspace':workspace.relative_to(ROOT).as_posix()}
    start=time.monotonic();agent=None
    try:
        model=_build_model_client(args)
        model.on_transport_event=lambda event:append_json(directory/'transport.jsonl',event)
        recorder=TransportRecordingClient(model,directory/'model_io.jsonl');recorder.model=model.model;recorder.base_url=model.base_url
        context=WorkspaceContext(str(workspace),str(workspace),'-','-','fresh fixture',[],{'README.md':task['files']['README.md']})
        agent=Pico(recorder,context,SessionStore(workspace/'.pico/sessions'),approval_policy='ask',
            max_steps=20,max_new_tokens=3072,max_depth=0,verification_command=VERIFY_COMMAND,
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
            event={'name':name,'allowed':allowed,'args':arguments,'at':utcnow()}
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
    row['changed_files']=changed
    row['scope_valid']=not (set(changed)-set(task['editable_files'])) and all(after.get(n)==v for n,v in before.items() if n not in task['editable_files'])
    row['protected_files_unchanged']=all(after.get(n)==v for n,v in before.items() if n not in task['editable_files'])
    patches=[]
    for name in changed:
        p=workspace/name
        patches.extend(difflib.unified_diff(task['files'].get(name,'').splitlines(True),p.read_text(encoding='utf-8',errors='replace').splitlines(True) if p.is_file() else [],fromfile='a/'+name,tofile='b/'+name))
    (directory/'changes.diff').write_text(''.join(patches),encoding='utf-8',newline='\n')
    implementation={n:(workspace/n).read_bytes() for n in task['editable_files'] if (workspace/n).is_file()}
    row['external_verifier']=verify_pristine(task,implementation,directory/'external_final')
    row['functional_passed']=bool(row['scope_valid'] and len(implementation)==len(task['editable_files']) and row['external_verifier']['accepted'])
    state=getattr(agent,'current_task_state',None);events=[]
    if state:
        row.update(run_id=state.run_id,status=state.status,stop_reason=state.stop_reason,attempts=state.attempts)
        trace=agent.run_store.trace_path(state);events=[json.loads(l) for l in trace.read_text(encoding='utf-8').splitlines()]
        row.update(trace=trace.relative_to(ROOT).as_posix(),trace_sha256=digest(trace),completion_verification=dict(agent.last_verification))
    row.update(trace_metrics(events,after,before))
    row['completion_rejections']=[e for e in events if e['event']=='completion_rejected']
    transport=[json.loads(l) for l in (directory/'transport.jsonl').read_text(encoding='utf-8').splitlines()] if (directory/'transport.jsonl').exists() else []
    row['transport_attempts']=len(transport);row['transport_failures']=sum(not e['ok'] for e in transport)
    for metric in ('input_tokens','output_tokens'):row[metric]['complete']=row[metric]['complete'] and not row['transport_failures'] and not row.get('error')
    row['workflow_passed']=bool(row['functional_passed'] and row.get('status')=='completed' and row.get('agent_verified_after_last_write') and not row.get('error'))
    row['failure_category']=('success' if row['workflow_passed'] else 'model_or_runtime_error' if row.get('error') else
                             'test_regression_or_incomplete_fix' if not row['functional_passed'] else 'missing_reverification_or_incomplete_workflow')
    row['elapsed_ms']=round((time.monotonic()-start)*1000);row['finished_at']=utcnow()
    frozen();write_json(directory/'result.json',row,exclusive=True)
    print(json.dumps({k:row[k] for k in ('run_key','functional_passed','workflow_passed','failure_category','transport_failures','elapsed_ms')}),flush=True)

def run(output):
    frozen();plan=load_json(VERSION/'plan.json');output.mkdir(parents=True,exist_ok=False)
    started_at=utcnow();write_json(output/'batch.json',{'started_at':started_at,'configuration':CONFIG,'freeze_sha256':digest(VERSION/'freeze.json'),'planned_runs':12},exclusive=True)
    pending=list(plan);active={}
    while pending or active:
        while pending and len(active)<3:
            slot=pending.pop(0);logs=output/'logs';logs.mkdir(exist_ok=True)
            log=(logs/(slot['run_key']+'.log')).open('wb')
            env=dict(os.environ,GIT_CEILING_DIRECTORIES=str(ROOT/'artifacts'),PYTHONDONTWRITEBYTECODE='1')
            p=subprocess.Popen([sys.executable,'-u','-X','utf8',str(Path(__file__).resolve()),'--worker',slot['run_key'],'--output',str(output)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            active[slot['run_key']]=(p,log,time.monotonic(),slot)
            print('START '+slot['run_key'],flush=True)
        for key,(p,log,start,slot) in list(active.items()):
            timeout=time.monotonic()-start>CONFIG['run_deadline_seconds']
            if p.poll() is None and not timeout:continue
            if p.poll() is None:p.terminate();p.wait(timeout=15)
            log.close();directory=output/'runs'/key;directory.mkdir(exist_ok=True,parents=True)
            if not (directory/'result.json').exists():
                task=next(t for t in load_json(SUITE/'tasks.json')['tasks'] if t['id']==slot['task_id'])
                write_json(directory/'result.json',{**slot,'category':task['category'],'functional_passed':False,'workflow_passed':False,
                    'failure_category':'run_deadline' if timeout else 'worker_error','error':f'worker exit {p.returncode}; no rerun',
                    'elapsed_ms':round((time.monotonic()-start)*1000)},exclusive=True)
            value=load_json(directory/'result.json');print(json.dumps({k:value.get(k) for k in ('run_key','workflow_passed','error','elapsed_ms')}),flush=True)
            del active[key]
            rows=[load_json(p) for p in (output/'runs').glob('*/result.json')]
            write_json(output/'progress.json',{'completed_runs':len(rows),'planned_runs':12,'summary':aggregate(rows)})
        if active:time.sleep(.25)
    rows=[load_json(output/'runs'/p['run_key']/'result.json') for p in plan];assert len(rows)==12
    frozen();write_json(output/'results.json',{'completed':True,'started_at':started_at,'finished_at':utcnow(),
        'configuration':CONFIG,'freeze_sha256':digest(VERSION/'freeze.json'),'summary':aggregate(rows),'rows':rows},exclusive=True)
    print(json.dumps(aggregate(rows)),flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze',action='store_true');parser.add_argument('--check',action='store_true');parser.add_argument('--worker')
    parser.add_argument('--output',default='artifacts/repair-regression-v2-2026-10-24')
    args=parser.parse_args();output=(ROOT/args.output).resolve()
    if ROOT not in output.parents:parser.error('output must be inside project')
    if args.freeze:freeze()
    elif args.check:print(json.dumps({'freeze_sha256':digest(VERSION/'freeze.json'),'files':len(frozen()['hashes'])}))
    elif args.worker:execute(output,args.worker)
    else:run(output)

if __name__=='__main__':main()
