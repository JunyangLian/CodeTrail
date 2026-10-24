"""Frozen repair-suite execution, pristine external verification and trace metrics.

The benchmark controller is outside the agent workspace. Only task fixtures,
visible acceptance tests and task instructions are installed inside it.
"""
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import time

from ..cli import _build_model_client, _configured_secret_names, build_arg_parser
from ..config import load_project_env
from ..runtime import DEFAULT_SHELL_ENV_ALLOWLIST, Pico, SessionStore
from ..security import redact_artifact, shell_env
from ..workspace import WorkspaceContext

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / 'benchmarks/repair_suite_v1'
VERIFY_COMMAND = 'python -B -m unittest discover -v'
CATEGORIES = {'single_file':5, 'cross_file':7, 'unknown_location':4, 'regression_compatibility':4}
PROTOCOL = {'model':'zai-org/GLM-5.3-Flash', 'provider':'openai',
            'base_url':'https://api.siliconflow.cn/v1', 'api_format':'chat_completions',
            'temperature':0.2, 'request_timeout_seconds':120, 'provider_transport_attempts':3,
            'max_steps':20, 'max_new_tokens':3072, 'run_deadline_seconds':900,
            'external_verifier_timeout_seconds':25, 'repetitions':3, 'concurrency':3,
            'schedule_seed':20261024, 'verify_command':VERIFY_COMMAND,
            'tools':['list_files','read_file','search','patch_file','write_file','run_shell'],
            'memory':'fresh default state per run', 'acceptance_tests':'visible, pristine external rerun',
            'retries':'No task reruns or replacements; provider transport retries are within a run.'}

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_json(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Per-run files have one writer. Completed records are written exclusively.
    with path.open('x' if exclusive else 'w', encoding='utf-8', newline='\n') as stream:
        json.dump(redact_artifact(value), stream, ensure_ascii=False, indent=2)
        stream.write('\n')

def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def append_json(path, value):
    with Path(path).open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(redact_artifact(value), ensure_ascii=False)+'\n')
        stream.flush()

def safe_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if root not in path.parents or path.is_symlink():
        raise ValueError('fixture path escapes root')
    return path

def install(workspace, files):
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        path = safe_path(workspace, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8', newline='\n')

def inventory(workspace):
    result = {}
    for path in sorted(Path(workspace).rglob('*')):
        rel = path.relative_to(workspace)
        if any(part in {'.pico','__pycache__','.pytest_cache'} for part in rel.parts):
            continue
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            result[rel.as_posix()] = 'REPARSE_POINT'
        elif path.is_file():
            result[rel.as_posix()] = digest(path)
    return result

def verify_pristine(task, implementation, destination):
    """Install frozen tests anew, overlay only eligible implementations, then execute."""
    install(destination, task['files'])
    for name, data in implementation.items():
        if name not in task['editable_files']:
            raise ValueError('external verifier received a noneditable overlay')
        safe_path(destination, name).write_bytes(data)
    env = shell_env(allowlist=DEFAULT_SHELL_ENV_ALLOWLIST, root=str(destination))
    env.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable,'-I','-B',str(ROOT/'scripts/repair_external_verifier.py'),str(destination)]
    started = time.monotonic()
    try:
        process = subprocess.run(command, cwd=destination, env=env, capture_output=True,
                                 timeout=PROTOCOL['external_verifier_timeout_seconds'])
        stdout = process.stdout.decode('utf-8', errors='replace')
        stderr = process.stderr.decode('utf-8', errors='replace')
        try:
            payload = json.loads(stdout)
        except ValueError:
            payload = {'successful':False,'tests_run':None,'output':stdout,'error':'invalid verifier result'}
        payload.update(exit_code=process.returncode, stderr=stderr)
    except subprocess.TimeoutExpired as exc:
        payload = {'successful':False,'tests_run':None,'exit_code':None,'timed_out':True,
                   'output':str(exc), 'error':'external verifier timeout'}
    payload['elapsed_ms'] = round((time.monotonic()-started)*1000)
    payload['accepted'] = bool(payload.get('successful') and payload['exit_code']==0
                               and payload.get('tests_run')==task['expected_tests']
                               and not any(payload.get(k,0) for k in ('skipped','expected_failures','unexpected_successes')))
    return payload

def freeze_suite():
    if (SUITE/'freeze.json').exists():
        raise ValueError('Already frozen; do not regenerate or modify the gold.')
    tasks = load_json(SUITE/'tasks.json')['tasks']
    assert len(tasks)==len({task['id'] for task in tasks})==20
    assert Counter(task['category'] for task in tasks)==CATEGORIES
    references = load_json(SUITE/'reference_solutions.json')
    preflight_root = ROOT/('artifacts/repair-suite-v1-preflight-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    preflight_root.mkdir(exist_ok=False, parents=True)
    rows = []
    for task in tasks:
        assert task['sha256']=={name:hashlib.sha256(content.encode()).hexdigest() for name,content in task['files'].items()}
        buggy = verify_pristine(task, {}, preflight_root/(task['id']+'-buggy'))
        repaired = verify_pristine(task, {name:content.encode() for name,content in references[task['id']].items()}, preflight_root/(task['id']+'-reference'))
        row = {'task_id':task['id'],'buggy':buggy,'reference':repaired}
        rows.append(row)
        print(json.dumps({'preflight':task['id'],'buggy_tests':buggy.get('tests_run'),'buggy_failed':not buggy['accepted'],'reference_pass':repaired['accepted']}),flush=True)
    write_json(SUITE/'preflight.json', {'kind':'verifier sanity check, no model calls', 'rows':rows})
    failures = [row['task_id'] for row in rows if row['buggy']['accepted'] or row['buggy'].get('tests_run')!=next(t['expected_tests'] for t in tasks if t['id']==row['task_id']) or not row['reference']['accepted']]
    if failures:
        raise ValueError('Preflight failed before model execution: '+', '.join(failures))
    plan = [{'task_id':task['id'],'repeat':repeat} for repeat in range(1,4) for task in tasks]
    random.Random(PROTOCOL['schedule_seed']).shuffle(plan)
    for index,row in enumerate(plan,1):
        row['run_key']=f"{index:02d}-{row['task_id']}-r{row['repeat']}"
    write_json(SUITE/'protocol.json',PROTOCOL)
    write_json(SUITE/'plan.json',plan)
    # Include all source evidence and harness code, not credentials or result output.
    files = list(SUITE.rglob('*')) + list((ROOT/'pico').rglob('*.py'))
    files += [ROOT/'scripts'/name for name in ('run_repair_suite.py','repair_external_verifier.py')]
    hashes = {path.relative_to(ROOT).as_posix():digest(path) for path in files if path.is_file() and '__pycache__' not in path.parts}
    freeze = {'schema_version':1, 'frozen_at':utcnow(), 'hashes':hashes,
              'unique_tasks':20,'planned_runs':60,'upstream_tasks':8,'upstream_projects':2,
              'python_version':platform.python_version(),'platform':platform.platform(),
              'policy':'No gold, prompt, model, budget or runtime modifications during the batch. Preserve every planned run including errors; never put references or upstream fix metadata in agent workspace.'}
    write_json(SUITE/'freeze.json',freeze,exclusive=True)
    print(json.dumps({'freeze_sha256':digest(SUITE/'freeze.json'),'files':len(hashes)}),flush=True)

def check_freeze():
    freeze = load_json(SUITE/'freeze.json')
    for relative, expected in freeze['hashes'].items():
        if digest(safe_path(ROOT,relative))!=expected:
            raise ValueError('Frozen asset changed: '+relative)
    if load_json(SUITE/'protocol.json')!=PROTOCOL:
        raise ValueError('Protocol changed')
    return freeze

class RecordingClient:
    """Preserve every prompt, untruncated model reply and observed provider usage."""
    def __init__(self, client, output):
        self.client = client
        self.output = output
        self.supports_prompt_cache = client.supports_prompt_cache
        self.last_completion_metadata = {}
        self.index = 0
    def complete(self, prompt, max_new_tokens, **kwargs):
        self.index += 1
        started = time.monotonic()
        append_json(self.output, {'event':'request','index':self.index,'at':utcnow(),'prompt':prompt,'max_new_tokens':max_new_tokens})
        try:
            answer = self.client.complete(prompt,max_new_tokens,**kwargs)
            self.last_completion_metadata = dict(self.client.last_completion_metadata)
            append_json(self.output,{'event':'response','index':self.index,'at':utcnow(),'raw_text':answer,
                                    'usage':self.last_completion_metadata,'elapsed_ms':round((time.monotonic()-started)*1000)})
            return answer
        except Exception as exc:
            self.last_completion_metadata = dict(self.client.last_completion_metadata)
            append_json(self.output,{'event':'error','index':self.index,'at':utcnow(),'error':str(exc),
                                    'usage':self.last_completion_metadata,'elapsed_ms':round((time.monotonic()-started)*1000)})
            raise

def collect_trace(workspace, agent):
    state = getattr(agent,'current_task_state',None)
    if not state:
        return [],None
    trace = workspace/'.pico/runs'/state.run_id/'trace.jsonl'
    return [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()], trace

def classify(row):
    if row.get('functional_passed') and row.get('workflow_passed'):
        return 'success'
    if row.get('controller_timeout'):
        return 'run_deadline'
    if row.get('error'):
        return 'model_or_runtime_error'
    if not row.get('scope_valid',False):
        return 'protected_file_or_scope_violation'
    if not row.get('changed_files'):
        return 'no_code_change'
    if not row.get('functional_passed'):
        return 'test_regression_or_incomplete_fix'
    return 'missing_reverification_or_incomplete_workflow'

def trace_metrics(events, final_inventory, initial_inventory):
    parsed = [e for e in events if e['event']=='model_parsed']
    tools = [e for e in events if e['event']=='tool_executed']
    verified = [e for e in tools if e.get('name')=='run_shell' and e.get('args',{}).get('command','').strip()==VERIFY_COMMAND and e.get('exit_code')==0]
    last_write = max((i for i,e in enumerate(events) if e['event']=='tool_executed' and e.get('workspace_changed')), default=-1)
    last_verified = max((i for i,e in enumerate(events) if e in verified), default=-1)
    usage = [e.get('completion_metadata',{}) for e in parsed]
    def tokens(name):
        values = [item.get(name) for item in usage]
        return {'observed_total':sum(v for v in values if isinstance(v,int)),
                'observed_calls':sum(isinstance(v,int) for v in values),'successful_calls':len(values),
                'complete':bool(values) and all(isinstance(v,int) for v in values)}
    return {'input_tokens':tokens('input_tokens'),'output_tokens':tokens('output_tokens'),
            'successful_model_calls':len(parsed), 'tool_steps':len(tools),
            'agent_verified_after_last_write':last_verified>last_write and last_verified>=0,
            'tool_issues':[{'trace_line':i+1,'name':e.get('name'),'code':e.get('tool_error_code'),'exit_code':e.get('exit_code')} for i,e in enumerate(events) if e['event']=='tool_executed' and e.get('tool_status')!='ok'],
            'security_events':[{'trace_line':i+1,'name':e.get('name'),'type':e.get('security_event_type'),'args':e.get('args')} for i,e in enumerate(events) if e['event']=='tool_executed' and e.get('security_event_type')],
            'final_state_matches_agent_verification':last_verified>last_write}

def execute_run(output, run_key, env_file):
    check_freeze()
    plan = next(row for row in load_json(SUITE/'plan.json') if row['run_key']==run_key)
    task = next(task for task in load_json(SUITE/'tasks.json')['tasks'] if task['id']==plan['task_id'])
    directory = Path(output)/'runs'/run_key
    directory.mkdir(parents=True,exist_ok=False)
    write_json(directory/'started.json',{'at':utcnow(),**plan,'freeze_sha256':digest(SUITE/'freeze.json')},exclusive=True)
    workspace = directory/'workspace'
    install(workspace,task['files'])
    before = inventory(workspace)
    assert before==task['sha256']
    # Credentials are loaded only by the harness process and never installed in the fixture.
    load_project_env(Path(env_file).parent)
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    os.environ['PYTHONIOENCODING']='utf-8'
    args = build_arg_parser().parse_args(['--provider','openai','--model',PROTOCOL['model'],
            '--base-url',PROTOCOL['base_url'],'--api-format','chat_completions',
            '--temperature',str(PROTOCOL['temperature']),'--openai-timeout','120'])
    row = {**plan,'category':task['category'],'origin_kind':task['origin']['kind'],
           'started_at':utcnow(),'approvals':[],'functional_passed':False,'workflow_passed':False,
           'workspace':workspace.relative_to(ROOT).as_posix(),'initial_sha256':before}
    started = time.monotonic()
    agent = None
    try:
        client = RecordingClient(_build_model_client(args),directory/'model_io.jsonl')
        # Do not query the enclosing project's Git history or reference-bearing docs.
        context = WorkspaceContext(str(workspace),str(workspace),'-','-', 'fresh fixture',[],
                                   {'README.md':task['files']['README.md']})
        agent = Pico(model_client=client,workspace=context,session_store=SessionStore(workspace/'.pico/sessions'),
                     approval_policy='ask',max_steps=PROTOCOL['max_steps'],max_new_tokens=PROTOCOL['max_new_tokens'],
                     max_depth=0,allowed_tools=PROTOCOL['tools'],
                     shell_env_allowlist=DEFAULT_SHELL_ENV_ALLOWLIST+('PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING'),
                     secret_env_names=_configured_secret_names(args))
        assert not agent.session['history']
        row['session_id']=agent.session['id']
        def approve(name, arguments):
            allowed=False
            if name=='run_shell':
                allowed=arguments.get('command','').strip()==VERIFY_COMMAND
            elif name in {'patch_file','write_file'}:
                try:
                    rel=agent.path(arguments.get('path','')).relative_to(workspace).as_posix()
                    allowed=rel in task['editable_files']
                except (ValueError,TypeError):
                    pass
            record={'name':name,'allowed':allowed,'path':arguments.get('path'),'command':arguments.get('command'),'at':utcnow()}
            row['approvals'].append(record)
            append_json(directory/'approvals.jsonl',record)
            return allowed
        agent.approve=approve
        # Record untruncated tool outputs independently of the context clipping policy.
        for name,tool in agent.tools.items():
            original=tool['run']
            def recording(arguments, original=original, name=name):
                value=original(arguments)
                append_json(directory/'tool_io.jsonl',{'name':name,'args':arguments,'output':str(value),'at':utcnow()})
                return value
            tool['run']=recording
        row['answer']=agent.ask(task['prompt'])
    except BaseException as exc:
        row['error']=f'{type(exc).__name__}: {exc}'
    row['agent_elapsed_ms']=round((time.monotonic()-started)*1000)
    after=inventory(workspace)
    row['final_sha256']=after
    changed=[name for name in sorted(before.keys()|after.keys()) if before.get(name)!=after.get(name)]
    row['changed_files']=changed
    row['scope_valid']=set(changed)<=set(task['editable_files']) and all(after.get(name)==value for name,value in before.items() if name not in task['editable_files'])
    row['protected_files_unchanged']=all(after.get(name)==value for name,value in before.items() if name not in task['editable_files'])
    patches=[]
    for name in changed:
        original=task['files'].get(name,'')
        path=workspace/name
        updated=path.read_text(encoding='utf-8',errors='replace') if path.is_file() else ''
        patches.extend(difflib.unified_diff(original.splitlines(True),updated.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    (directory/'changes.diff').write_text(''.join(patches),encoding='utf-8',newline='\n')
    implementation={name:(workspace/name).read_bytes() for name in task['editable_files'] if (workspace/name).is_file()}
    # Missing implementations are explicitly invalid, never silently restored to pass.
    row['all_implementations_present']=len(implementation)==len(task['editable_files'])
    row['external_verifier']=verify_pristine(task,implementation,directory/'external_final')
    row['functional_passed']=bool(row['external_verifier']['accepted'] and row['scope_valid'] and row['all_implementations_present'])
    events,trace=collect_trace(workspace,agent)
    row.update(trace_metrics(events,after,before))
    state=getattr(agent,'current_task_state',None)
    if state:
        row.update(run_id=state.run_id,status=state.status,stop_reason=state.stop_reason,attempts=state.attempts)
    row['trace']=trace.relative_to(ROOT).as_posix() if trace else None
    row['trace_sha256']=digest(trace) if trace else None
    row['workflow_passed']=bool(row['functional_passed'] and row.get('status')=='completed' and not row.get('error') and row['agent_verified_after_last_write'])
    row['failure_category']=classify(row)
    row['elapsed_ms']=round((time.monotonic()-started)*1000)
    row['finished_at']=utcnow()
    row['freeze_sha256']=digest(SUITE/'freeze.json')
    check_freeze()
    write_json(directory/'result.json',row,exclusive=True)
    print(json.dumps({key:row[key] for key in ('run_key','functional_passed','workflow_passed','failure_category','tool_steps','elapsed_ms')}),flush=True)
    return row

def aggregate(rows):
    groups={}
    for task_id in sorted({row['task_id'] for row in rows}):
        subset=[row for row in rows if row['task_id']==task_id]
        groups[task_id]={'runs':len(subset),'functional_passes':sum(row.get('functional_passed',False) for row in subset),
                         'workflow_passes':sum(row.get('workflow_passed',False) for row in subset)}
    count=len(rows)
    functional=sum(row.get('functional_passed',False) for row in rows)
    workflow=sum(row.get('workflow_passed',False) for row in rows)
    def metric(name):
        observed=[row[name] for row in rows if isinstance(row.get(name),(int,float))]
        return {'observed_runs':len(observed),'total':sum(observed),'mean':sum(observed)/len(observed) if observed else None}
    return {'unique_tasks':len(groups),'runs':count,'functional_passes':functional,'functional_rate':functional/count if count else None,
            'workflow_passes':workflow,'workflow_rate':workflow/count if count else None,
            'failure_categories':dict(Counter(row['failure_category'] for row in rows if row['failure_category']!='success')),
            'tool_steps':metric('tool_steps'),'elapsed_ms':metric('elapsed_ms'),
            'input_tokens_observed':sum(row.get('input_tokens',{}).get('observed_total',0) for row in rows),
            'output_tokens_observed':sum(row.get('output_tokens',{}).get('observed_total',0) for row in rows),
            'token_complete_runs':sum(row.get('input_tokens',{}).get('complete',False) and row.get('output_tokens',{}).get('complete',False) for row in rows),
            'security_blocked_events':sum(len(row.get('security_events',[])) for row in rows),
            'unauthorized_final_change_runs':sum(not row.get('scope_valid',True) for row in rows),
            'per_task':groups,
            'per_category':{category:{'runs':len(subset),'functional_passes':sum(row.get('functional_passed',False) for row in subset),'workflow_passes':sum(row.get('workflow_passed',False) for row in subset)}
                            for category in CATEGORIES if (subset:=[row for row in rows if row['category']==category])}}
