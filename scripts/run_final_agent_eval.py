"""Final fixed 20x3 + unseen-to-project 10x3 evaluation. No task replacement."""
from collections import Counter
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.providers.clients import OpenAICompatibleModelClient
from pico.runtime import Pico,SessionStore,DEFAULT_SHELL_ENV_ALLOWLIST
from pico.workspace import WorkspaceContext
from pico.evaluation.repair_suite import (RecordingClient,append_json,digest,install,inventory,load_json,
                                         trace_metrics,utcnow,verify_pristine,write_json,VERIFY_COMMAND)
from pico.evaluation.baseline_assets import verify_baseline_assets
from pico.evaluation.seven_dimensions import run_metrics,aggregate_metrics

ASSETS=ROOT/'benchmarks/final_agent_v1'
DEFAULT=ROOT/'artifacts/final-agent-90-2026-10-25'
MODEL='deepseek-ai/DeepSeek-V4.1-Flash'
CONFIG={'schema_version':1,'provider':'siliconflow','base_url':'https://api.siliconflow.cn/v1',
        'model':MODEL,'documented_deployment':'DeepSeek-V4.1-Flash-1017 per 2026-10-21 provider notice',
        'version_limit':'Provider exposes mutable model ID, not an immutable weight revision. Record returned ID/fingerprint and pre/post metadata; never silently substitute.',
        'api_format':'chat_completions','native_tools':True,'enable_thinking':True,'thinking_budget':4096,
        'max_new_tokens':4096,'budget_semantics':'SiliconFlow documents max_tokens excluding reasoning; thinking_budget is separately requested. Effectiveness checked against returned usage, not assumed.',
        'temperature':.2,'top_p':.95,'sampling_effectiveness':'Requested fixed values; provider does not expose effective sampler settings.',
        'request_timeout_seconds':180,'max_transport_attempts':3,'max_steps':20,'max_attempts':60,
        'concurrency':3,'run_deadline_seconds':900,'external_verifier_timeout_seconds':25,
        'repetitions':3,'schedule_seed':20261025,'verification_command':VERIFY_COMMAND,
        'tools':['list_files','read_file','search','patch_file','write_file','run_shell'],
        'approval':'ask; only exact verifier shell command and frozen editable files approved; no ACL changes',
        'isolation':'Fresh workspace, client and session per run; child shell secret environment removed; application boundary is not an OS sandbox.',
        'credential_source':'SILICONFLOW_API_KEY environment variable only; no dotenv loading',
        'retry_policy':'No task reruns/replacements. Transport and Windows persistence bounded retries are internal and audited.',
        'raw_wire':'Full provider request/response bodies retained without Authorization headers; includes third-party API-returned reasoning for authorized public benchmark audit only.'}


def events(path):
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def freeze(output):
    baseline=verify_baseline_assets();tasks=load_json(ASSETS/'tasks.json')['tasks']
    assert len(tasks)==30 and sum(t['cohort']=='holdout' for t in tasks)==10
    quality=load_json(ASSETS/'asset_quality.json')
    assert len(quality)==10 and all(not x['buggy']['accepted'] and x['fixed']['accepted'] for x in quality)
    probe=load_json(ROOT/'artifacts/final-eval-preflight-2026-10-25/compatibility-probe-v2.json')
    assert probe['compatible'] and probe['model']==MODEL
    registry=load_json(ROOT/'artifacts/final-eval-preflight-2026-10-25/siliconflow-models.json')
    assert MODEL in {m['id'] for m in registry['data']}
    output.mkdir(parents=True,exist_ok=False)
    plan=[{'task_id':t['id'],'cohort':t['cohort'],'repeat':r} for t in tasks for r in (1,2,3)]
    random.Random(CONFIG['schedule_seed']).shuffle(plan)
    for i,slot in enumerate(plan,1):slot['run_key']=f"{i:02d}-{slot['task_id']}-r{slot['repeat']}"
    write_json(output/'plan.json',plan,exclusive=True);write_json(output/'protocol.json',CONFIG,exclusive=True)
    sources=list((ROOT/'pico').rglob('*.py'))+list(ASSETS.rglob('*'))+[Path(__file__).resolve(),
             ROOT/'scripts/repair_external_verifier.py',ROOT/'scripts/build_final_assets.py',
             ROOT/'scripts/run_final_security.py',ROOT/'scripts/report_final_agent_eval.py']
    sources += list((ROOT/'tests').rglob('*'))
    sources=[p for p in sources if p.is_file() and '__pycache__' not in p.parts]
    hashes={p.relative_to(ROOT).as_posix():digest(p) for p in sources}
    write_json(output/'freeze.json',{'at':utcnow(),'baseline':baseline,'configuration':CONFIG,
             'hashes':hashes,'plan_sha256':digest(output/'plan.json'),'protocol_sha256':digest(output/'protocol.json'),
             'provider_registry_sha256':digest(ROOT/'artifacts/final-eval-preflight-2026-10-25/siliconflow-models.json'),
             'private_gold_sha256':digest(ROOT/'artifacts/final-private-gold-2026-10-25/holdout_references.json'),
             'compatibility_probe_sha256':digest(ROOT/'artifacts/final-eval-preflight-2026-10-25/compatibility-probe-v2.json')},exclusive=True)
    write_json(output/'private-gold-custody.json',{'sha256':digest(ROOT/'artifacts/final-private-gold-2026-10-25/holdout_references.json'),
               'policy':'Controller-only, excluded from source snapshot, Agent workspace and public evidence package.'},exclusive=True)
    with zipfile.ZipFile(output/'source_snapshot.zip','x',zipfile.ZIP_DEFLATED) as archive:
        for name in hashes:archive.write(ROOT/name,name)
        archive.writestr('SOURCE_MANIFEST.json',json.dumps(hashes,indent=2))
    print(json.dumps({'frozen_tasks':30,'planned_runs':90,'sha256':digest(output/'freeze.json')}),flush=True)


def check(output):
    frozen=load_json(output/'freeze.json')
    assert frozen['configuration']==CONFIG
    for name,h in frozen['hashes'].items():assert digest(ROOT/name)==h,name
    assert digest(output/'plan.json')==frozen['plan_sha256']
    assert digest(output/'protocol.json')==frozen['protocol_sha256']


def client():
    key=os.environ.get('SILICONFLOW_API_KEY')
    if not key:raise RuntimeError('SILICONFLOW_API_KEY environment variable is missing; no fallback credential/model')
    model=OpenAICompatibleModelClient(MODEL,CONFIG['base_url'],key,CONFIG['temperature'],180,'chat_completions')
    model.top_p=CONFIG['top_p'];assert model.supports_native_tools
    return model


class Recorder(RecordingClient):
    @property
    def on_transport_event(self):return self.client.on_transport_event
    @on_transport_event.setter
    def on_transport_event(self,value):self.client.on_transport_event=value


def failure_category(row,transport):
    if row.get('controller_timeout'):return 'controller_run_deadline'
    error=row.get('error','')
    if error:
        if 'PermissionError' in error:return 'state_or_filesystem_persistence_error'
        if 'timed out' in error.lower() or 'TimeoutError' in error:
            stage=next((e.get('phase') for e in reversed(transport) if not e['ok']),None)
            return 'api_'+(stage or 'unclassified')+'_timeout'
        if any(e.get('status_code')==429 for e in transport):return 'api_rate_limit'
        if any(e.get('status_code') in (401,403) for e in transport):return 'api_authorization'
        return 'provider_or_runtime_error'
    if not row['scope_valid'] or not row['protected_files_unchanged']:return 'scope_or_protected_file_violation'
    if not row['functional_passed']:return 'no_code_change' if not row['changed_files'] else 'incomplete_fix_or_regression'
    if not row['workflow_passed']:return 'functional_pass_but_workflow_incomplete'
    return 'success'


def fatal_provider_reason(row):
    """A terminal account/model error stops new work; old observations are retained."""
    import re
    error=row.get('error') or ''
    match=re.search(r'\bHTTP\s+(401|402|403)\b',error)
    if match:return {'401':'api_authorization','402':'api_billing_insufficient_balance','403':'api_authorization'}[match.group(1)]
    if row.get('failure_category')=='api_authorization':return 'api_authorization'
    if 'different model ID' in error:return 'provider_model_mismatch'
    return None


def finalize(output,slot,error=None,controller_timeout=False):
    tasks={t['id']:t for t in load_json(ASSETS/'tasks.json')['tasks']};task=tasks[slot['task_id']]
    directory=output/'runs'/slot['run_key'];workspace=directory/'workspace'
    start=load_json(directory/'started.json') if (directory/'started.json').exists() else {'at':utcnow(),'time':time.time()}
    before=task['sha256'];after=inventory(workspace) if workspace.exists() else {}
    row={**slot,'category':task['category'],'origin_kind':task['origin']['kind'],'development_tuned':task.get('development_tuned',False),
         'started_at':start['at'],'finished_at':utcnow(),'elapsed_ms':round((time.time()-start['time'])*1000),
         'verify_command':VERIFY_COMMAND,'workspace':workspace.relative_to(ROOT).as_posix(),
         'initial_sha256':before,'final_sha256':after,'controller_timeout':controller_timeout}
    if error:row['error']=error
    changed=[n for n in sorted(set(before)|set(after)) if before.get(n)!=after.get(n)]
    row.update(changed_files=changed,scope_valid=set(changed)<=set(task['editable_files']),
               protected_files_unchanged=all(after.get(n)==h for n,h in before.items() if n not in task['editable_files']))
    patches=[]
    for name in changed:
        p=workspace/name
        current=p.read_text(encoding='utf-8',errors='replace') if p.is_file() else ''
        patches.extend(difflib.unified_diff(task['files'].get(name,'').splitlines(True),current.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    (directory/'changes.diff').write_text(''.join(patches),encoding='utf-8',newline='\n')
    implementation={n:(workspace/n).read_bytes() for n in task['editable_files'] if (workspace/n).is_file()}
    external=directory/('external_parent_recovery' if (directory/'external_final').exists() else 'external_final')
    row['external_verifier']=verify_pristine(task,implementation,external)
    row['functional_passed']=bool(row['scope_valid'] and row['protected_files_unchanged'] and len(implementation)==len(task['editable_files']) and row['external_verifier']['accepted'])
    traces=list((workspace/'.pico/runs').glob('*/trace.jsonl')) if workspace.exists() else []
    ts=events(traces[0]) if len(traces)==1 else []
    if traces:row.update(trace=traces[0].relative_to(ROOT).as_posix(),trace_sha256=digest(traces[0]))
    states=list((workspace/'.pico/runs').glob('*/task_state.json')) if workspace.exists() else []
    state=load_json(states[0]) if len(states)==1 else {}
    sessions=list((workspace/'.pico/sessions').glob('*.json')) if workspace.exists() else []
    if sessions:row['session_id']=sessions[0].stem
    row.update(status=state.get('status'),stop_reason=state.get('stop_reason'),answer=state.get('final_answer') or start.get('answer',''))
    row.update(trace_metrics(ts,after,before))
    transport=events(directory/'transport.jsonl');row['transport_attempts']=len(transport);row['transport_failures']=sum(not t['ok'] for t in transport)
    row['protocol_errors']=sum(t['event']=='model_parsed' and t.get('kind')=='retry' for t in ts)
    row['protocol_error_types']=dict(Counter(t.get('completion_metadata',{}).get('protocol_error','unspecified') for t in ts if t['event']=='model_parsed' and t.get('kind')=='retry'))
    wire=events(directory/'wire_io.jsonl');tools=events(directory/'tool_io.jsonl');gold=load_json(ASSETS/'evidence_gold.json')[task['id']]
    passing=[t for t in tools if t['event']=='response' and t['name']=='run_shell' and t['args'].get('command','').strip()==VERIFY_COMMAND and t.get('metadata',{}).get('exit_code')==0]
    row['final_matches_last_passing_test']=bool(passing and passing[-1].get('workspace_sha256_after')==after)
    row['workflow_passed']=bool(row['functional_passed'] and state.get('status')=='completed' and row['agent_verified_after_last_write'] and row['final_matches_last_passing_test'] and not error and not controller_timeout)
    row['failure_category']=failure_category(row,transport)
    row['metrics']=run_metrics(task,row,tools,wire,gold)
    row['persistence_events']=[e for p in workspace.rglob('persistence.jsonl') for e in events(p)] if workspace.exists() else []
    row['freeze_sha256']=digest(output/'freeze.json')
    check(output);write_json(directory/'result.json',row,exclusive=True)
    return row


def worker(output,key):
    check(output);slot=next(s for s in load_json(output/'plan.json') if s['run_key']==key)
    task=next(t for t in load_json(ASSETS/'tasks.json')['tasks'] if t['id']==slot['task_id'])
    directory=output/'runs'/key;directory.mkdir(parents=True,exist_ok=False)
    workspace=directory/'workspace';install(workspace,task['files']);assert inventory(workspace)==task['sha256']
    write_json(directory/'started.json',{'at':utcnow(),'time':time.time(),'initial_sha256':inventory(workspace)},exclusive=True)
    os.environ.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));urllib.request.urlopen=opener.open
    recorder=None;error=None
    try:
        model=client();recorder=Recorder(model,directory/'model_io.jsonl');recorder.model=MODEL;recorder.base_url=model.base_url
        model.on_transport_event=lambda e:append_json(directory/'transport.jsonl',{'logical_request':recorder.index,'at':utcnow(),**e})
        def wire(e):
            assert os.environ['SILICONFLOW_API_KEY'] not in e['body'],'Credential in wire body; refusing exposure'
            append_json(directory/'wire_io.jsonl',{'index':recorder.index,'at':utcnow(),**e})
            if e['event']=='response':
                returned=json.loads(e['body']).get('model')
                if returned!=MODEL:raise RuntimeError('Provider returned a different model ID; no silent substitution')
        model.on_wire_event=wire
        context=WorkspaceContext(str(workspace),str(workspace),'-','-','fresh fixture',[],{'README.md':task['files'].get('README.md','')})
        agent=Pico(recorder,context,SessionStore(workspace/'.pico/sessions'),approval_policy='ask',
                   max_steps=20,max_new_tokens=4096,max_depth=0,verification_command=VERIFY_COMMAND,
                   allowed_tools=CONFIG['tools'],secret_env_names={'SILICONFLOW_API_KEY'},
                   shell_env_allowlist=DEFAULT_SHELL_ENV_ALLOWLIST+('PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING'))
        def approve(name,args):
            allowed=False
            if name=='run_shell':allowed=args.get('command','').strip()==VERIFY_COMMAND
            elif name in {'patch_file','write_file'}:
                try:allowed=agent.path(args.get('path','')).relative_to(workspace).as_posix() in task['editable_files']
                except (ValueError,TypeError):pass
            append_json(directory/'approvals.jsonl',{'name':name,'args':args,'allowed':allowed,'at':utcnow()})
            return allowed
        agent.approve=approve;original=agent.execute_tool;count=0
        def observe(name,args):
            nonlocal count
            count+=1
            append_json(directory/'tool_io.jsonl',{'event':'request','index':count,'name':name,'args':args,'model_request_index':recorder.index,'at':utcnow()})
            value=original(name,args)
            append_json(directory/'tool_io.jsonl',{'event':'response','index':count,'name':name,'args':args,'output':value.content,'metadata':value.metadata,'model_request_index':recorder.index,
                                                 'workspace_sha256_after':inventory(workspace),'at':utcnow()})
            return value
        agent.execute_tool=observe
        agent.ask(task['prompt'])
    except BaseException as exc:error=f'{type(exc).__name__}: {exc}'
    row=finalize(output,slot,error=error)
    print(json.dumps({k:row[k] for k in ('run_key','functional_passed','workflow_passed','failure_category','elapsed_ms')}),flush=True)


def run(output):
    check(output);client() # Validate environment-only credentials before launching work.
    write_json(output/'started.json',{'at':utcnow()},exclusive=True)
    plan=load_json(output/'plan.json');pending=list(plan);active={};(output/'logs').mkdir();not_started=[]
    while pending or active:
        while pending and len(active)<CONFIG['concurrency']:
            slot=pending.pop(0);key=slot['run_key'];log=(output/'logs'/(key+'.log')).open('xb')
            env=dict(os.environ,GIT_CEILING_DIRECTORIES=str(ROOT/'artifacts'),PYTHONDONTWRITEBYTECODE='1')
            process=subprocess.Popen([sys.executable,'-u','-X','utf8',str(Path(__file__).resolve()),'--output',str(output),'--worker',key],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            active[key]=(process,log,time.monotonic(),slot);print('START '+key,flush=True)
        for key,(process,log,start,slot) in list(active.items()):
            timeout=time.monotonic()-start>CONFIG['run_deadline_seconds']
            if process.poll() is None and not timeout:continue
            if process.poll() is None:
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True) if os.name=='nt' else process.terminate()
                process.wait(timeout=15)
            log.close();directory=output/'runs'/key;directory.mkdir(parents=True,exist_ok=True)
            if not (directory/'result.json').exists():
                finalize(output,slot,error='Original worker stopped; no replacement run',controller_timeout=timeout)
            row=load_json(directory/'result.json')
            print(json.dumps({k:row[k] for k in ('run_key','functional_passed','workflow_passed','failure_category','elapsed_ms')}),flush=True)
            del active[key]
            terminal=fatal_provider_reason(row)
            if pending and terminal:
                not_started=list(pending);pending.clear()
                write_json(output/'stopped.json',{'at':utcnow(),'reason':terminal,
                           'trigger_run':key,'not_started':not_started},exclusive=True)
            write_json(output/'progress.json',{'completed':len(list((output/'runs').glob('*/result.json'))),'planned':90})
        if active:time.sleep(.25)
    rows=[load_json(output/'runs'/r['run_key']/'result.json') for r in plan if (output/'runs'/r['run_key']/'result.json').exists()]
    check(output);write_json(output/'results.json',{'completed':len(rows)==90,'planned':90,'not_started':not_started,'at':utcnow(),'rows':rows,'summary':aggregate_metrics(rows)},exclusive=True)
    print(json.dumps({'completed':len(rows),'functional':sum(r['functional_passed'] for r in rows),'workflow':sum(r['workflow_passed'] for r in rows)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=DEFAULT)
    parser.add_argument('--freeze',action='store_true');parser.add_argument('--run',action='store_true');parser.add_argument('--worker')
    args=parser.parse_args();output=args.output.resolve()
    if ROOT not in output.parents:parser.error('Output must remain under this project')
    if args.freeze:freeze(output)
    elif args.worker:worker(output,args.worker)
    elif args.run:run(output)
    else:parser.print_help()
