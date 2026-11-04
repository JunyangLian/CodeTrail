"""Auditable offline integration scenario. Scripted decisions, real execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pico.operations import snapshot_hash
from pico.providers.clients import FakeModelClient
from pico.runtime import Pico,SessionStore
from pico.windows_sandbox import VERIFY,run_verification
from pico.workspace import WorkspaceContext
from run_workbench_demo import prepare
from workbench_demo_support import ScriptedRepairClient


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--shell-backend',choices=('appcontainer','host'),default='appcontainer')
    args=parser.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
    workspace=prepare(out/'workspace')
    protocol={'scope':'Offline scripted integration; not Agent repair benchmark','api_requests':0,'token_usage':None,
              'profile':'workbench','shell_backend':args.shell_backend,'verify_command':VERIFY,'step_budget':20,
              'runtime_sha256':{str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'pico').rglob('*.py'))},
              'assets_sha256':{p.relative_to(workspace).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in workspace.rglob('*') if p.is_file()}}
    save(out/'protocol.json',protocol)
    checks=[];started=time.monotonic()
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        if not value:raise AssertionError(name)
    client=ScriptedRepairClient()
    a=Pico(client,WorkspaceContext.build(workspace,repo_root_override=workspace),SessionStore(workspace/'.pico/sessions'),
           approval_policy='ask',max_steps=20,max_new_tokens=4096,max_depth=0,harness_profile='workbench',
           verification_command=VERIFY,shell_backend=args.shell_backend)
    client.agent=a
    approvals=[]
    def approve(name,values):
        allowed=name in {'patch_file','write_file','apply_changes','rewind_changes','mcp_docs'} or (name=='run_shell' and values.get('command')==VERIFY)
        approvals.append({'name':name,'allowed':allowed});return allowed
    a.approve=approve
    try:
        a.ask('Repair identifier normalization across directory.py and users.py; preserve tests and verify final changes.')
        check('real_configured_verification_passed',a.last_verification.get('status')=='passed')
        check('plan_completed_with_real_evidence',all(s['status']=='completed' and s['evidence'] for s in a.session['plan']))
        trace=Path(a.current_run_dir)/'trace.jsonl'
        events=[json.loads(line) for line in trace.read_text().splitlines()]
        exits=[e.get('exit_code') for e in events if e.get('event')=='tool_executed' and e.get('name')=='run_shell']
        check('actual_test_failure_then_repair',len(exits)==2 and exits[0]!=0 and exits[1]==0)
        check('acceptance_file_unchanged',hashlib.sha256((workspace/'test_directory.py').read_bytes()).hexdigest()==protocol['assets_sha256']['test_directory.py'])
        check('mcp_real_stdio_roundtrip',any(e.get('name')=='mcp_docs' and e.get('tool_status')=='ok' for e in events))
        check('skill_on_demand',any(e.get('name')=='load_skill' and e.get('tool_status')=='ok' for e in events))
        with a.memory.knowledge.connect() as db:
            candidate=dict(db.execute("SELECT * FROM memories WHERE fact_key='workflow:test-entry'").fetchone())
        check('prose_stays_candidate',candidate['status']=='candidate')
        a.memory.knowledge.activate(candidate['id'],candidate['version'],reviewer='explicit_scripted_demo_operator')
        active=a.memory.knowledge.active(a.session['id'])
        check('execution_history_linked_to_test',any(r['reviewer']=='host_verified_execution' and 'evidence_id' in r['text'] for r in active))
        read=next(e for e in events if e.get('name')=='read_file')
        key=read['evidence_id']
        check('original_evidence_recoverable',a.execute_tool('recall_evidence',{'evidence_id':key}).metadata['tool_status']=='ok')
        check('evidence_search_works',a.execute_tool('find_evidence',{'query':'directory.py'}).metadata['tool_status']=='ok')
        # Operator-side repository evolution: acceptance tests stay frozen.
        path=workspace/'directory.py'
        path.write_text('def normalize_user_id(value, *, case_sensitive=False):\n    text = str(value).strip()\n    return text if case_sensitive else text.casefold()\n',encoding='utf-8')
        after=a.memory.knowledge.active(a.session['id'])
        check('changed_source_memory_invalidated',not any(r['fact_key']=='source:directory.py' for r in after))
        check('unrelated_workflow_survives',any(r['fact_key']=='workflow:test-entry' for r in after))
        b=Pico.from_session(FakeModelClient(['<tool>{"name":"read_file","args":{"path":"directory.py"}}</tool>','<final>Read current source.</final>']),
            WorkspaceContext.build(workspace,repo_root_override=workspace),a.session_store,a.session['id'],
            harness_profile='workbench',approval_policy='never',read_only=True,max_depth=0)
        check('cross_session_workflow_recalled',any(n.get('memory_id')==candidate['id'] for n in b.memory.retrieval_candidates('Repository test entry')))
        b.ask('Inspect current normalize_user_id signature, do not edit.')
        check('dependent_plan_steps_invalidated',all(s['status']=='pending' for s in b.session['plan']))
        check('new_signature_reindexed',any('case_sensitive' in r['text'] for r in b.memory.knowledge.active() if r['fact_key']=='source:directory.py'))
        # Real child process exits after file effect, before operation completion.
        crash=("import sys,os\nfrom pathlib import Path\nsys.path.insert(0,"+repr(str(ROOT))+ ")\n"
               "from pico.runtime import Pico,SessionStore\nfrom pico.workspace import WorkspaceContext\nfrom pico.providers.clients import FakeModelClient\n"
               "p=Path("+repr(str(workspace))+")\na=Pico(FakeModelClient([]),WorkspaceContext.build(p,repo_root_override=p),SessionStore(p/'.pico/sessions'),harness_profile='workbench',approval_policy='auto')\n"
               "a.operations.complete=lambda *args,**kwargs:os._exit(77)\na.execute_tool('write_file',{'path':'recovery.txt','content':'effect persisted exactly once'})\n")
        result=subprocess.run([sys.executable,'-c',crash],capture_output=True,env=a.shell_env(),timeout=30)
        (out/'crash-stderr.log').write_bytes(result.stderr)
        check('actual_process_exit_after_effect',result.returncode==77 and (workspace/'recovery.txt').read_text()=='effect persisted exactly once')
        pending=a.operations.unresolved()
        check('unknown_effect_blocks_replay',bool(pending) and a.execute_tool('write_file',{'path':'another.txt','content':'blocked'}).metadata['tool_error_code']=='operation_recovery_required')
        for operation in pending:
            snap=a.capture_workspace_snapshot()
            a.operations.abandon(operation['id'],snapshot_hash(snap),'Scripted operator inspected recovery.txt and confirmed no replay',snap)
        check('explicit_reconciliation_preserves_effect',not a.operations.unresolved() and (workspace/'recovery.txt').read_text()=='effect persisted exactly once')
        # Guard probes are independent from the repair scenario success claim.
        guard_results=[]
        for name,values in [('read_file',{'path':'../outside.txt'}),('read_file',{'path':'.env'}),('read_file',{'path':'.pico/operations.sqlite3'})]:
            guard_results.append({'name':name,'path':values['path'],'rejected':a.execute_tool(name,values).metadata['tool_status']=='rejected'})
        a.approve=lambda *values:False
        guard_results.append({'name':'unapproved_shell','rejected':a.execute_tool('run_shell',{'command':VERIFY}).metadata['tool_error_code']=='approval_denied'})
        save(out/'guard-results.json',guard_results)
        check('four_mechanism_guards',all(r['rejected'] for r in guard_results))
        if args.shell_backend=='appcontainer':
            external=run_verification(workspace,VERIFY,30)
        else:
            run=subprocess.run([sys.executable,'-B','-m','unittest','discover','-v'],cwd=workspace,capture_output=True,text=True,timeout=30)
            external={'returncode':run.returncode,'output':run.stdout+run.stderr}
        save(out/'independent-verification.json',external)
        check('independent_current_version_passes',external['returncode']==0 and 'Ran 8 tests' in external['output'])
        check('frozen_acceptance_still_unchanged',hashlib.sha256((workspace/'test_directory.py').read_bytes()).hexdigest()==protocol['assets_sha256']['test_directory.py'])
        save(out/'approvals.json',approvals)
        summary={'status':'completed','checks':checks,'checks_passed':sum(c['passed'] for c in checks),'checks_total':len(checks),
                 'model':'scripted_fixture_no_llm','api_requests':0,'token_usage':None,'first_run_id':a.current_task_state.run_id,
                 'second_run_id':b.current_task_state.run_id,'shell_backend':args.shell_backend,'elapsed_seconds':round(time.monotonic()-started,3)}
    except Exception as exc:
        summary={'status':'failed','checks':checks,'error_type':type(exc).__name__,'detail':str(exc),
                 'model':'scripted_fixture_no_llm','api_requests':0,'elapsed_seconds':round(time.monotonic()-started,3)}
    save(out/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=True))
    return 0 if summary['status']=='completed' else 1


if __name__=='__main__':raise SystemExit(main())
