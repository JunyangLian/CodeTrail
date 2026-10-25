"""Additional credential recovery executions; never replace the first 90 observations."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.run_final_agent_eval import DEFAULT,CONFIG,check,finalize
from pico.evaluation.repair_suite import load_json,write_json,digest,utcnow

OUTPUT=ROOT/'artifacts/final-agent-credential-recovery-2026-10-25'
SAFETY=ROOT/'artifacts/final-agent-safety-credential-recovery-2026-10-25'


def recovery():
    check(DEFAULT)
    source=load_json(DEFAULT/'results.json')['rows']
    eligible=[r for r in source if 'HTTP 402' in r.get('error','') and '30001' in r.get('error','')]
    assert len(source)==90 and len(eligible)==54
    plan=load_json(DEFAULT/'plan.json');keys={r['run_key'] for r in eligible}
    selected=[s for s in plan if s['run_key'] in keys]
    OUTPUT.mkdir(parents=True,exist_ok=False)
    for name in ('freeze.json','protocol.json','plan.json','source_snapshot.zip','private-gold-custody.json'):
        shutil.copyfile(DEFAULT/name,OUTPUT/name)
    write_json(OUTPUT/'recovery-protocol.json',{
      'at':utcnow(),'reason':'User supplied replacement credential after documented account exhaustion.',
      'selection':'All and only first-batch HTTP 402/code30001 interruptions, in original frozen schedule order.',
      'original_batch':DEFAULT.relative_to(ROOT).as_posix(),'original_results_sha256':digest(DEFAULT/'results.json'),
      'original_run_hashes':{r['run_key']:digest(DEFAULT/'runs'/r['run_key']/'result.json') for r in source},
      'additional_executions':len(selected),'slots':selected,'script_sha256':digest(Path(__file__)),
      'policy':'Additional fresh executions, not resumed sessions or replacements. First90 remain primary; report additional54 separately. No performance-based selection, source/gold/parameter changes, or further task retries.',
      'configuration_unchanged':CONFIG},exclusive=True)
    write_json(OUTPUT/'started.json',{'at':utcnow(),'additional_not_original':True},exclusive=True)
    (OUTPUT/'logs').mkdir();pending=list(selected);active={};not_started=[]
    while pending or active:
        while pending and len(active)<CONFIG['concurrency']:
            slot=pending.pop(0);key=slot['run_key'];log=(OUTPUT/'logs'/(key+'.log')).open('xb')
            env=dict(os.environ,GIT_CEILING_DIRECTORIES=str(ROOT/'artifacts'),PYTHONDONTWRITEBYTECODE='1')
            p=subprocess.Popen([sys.executable,'-u','-X','utf8',str(ROOT/'scripts/run_final_agent_eval.py'),'--output',str(OUTPUT),'--worker',key],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            active[key]=(p,log,time.monotonic(),slot);print('RECOVERY START '+key,flush=True)
        for key,(p,log,start,slot) in list(active.items()):
            deadline=time.monotonic()-start>CONFIG['run_deadline_seconds']
            if p.poll() is None and not deadline:continue
            if p.poll() is None:
                subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True) if os.name=='nt' else p.terminate()
                p.wait(timeout=15)
            log.close();directory=OUTPUT/'runs'/key;directory.mkdir(parents=True,exist_ok=True)
            if not (directory/'result.json').exists():finalize(OUTPUT,slot,error='Recovery worker stopped; no replacement execution',controller_timeout=deadline)
            row=load_json(directory/'result.json');del active[key]
            print(json.dumps({k:row.get(k) for k in ('run_key','functional_passed','workflow_passed','failure_category','error')}),flush=True)
            if pending and any(text in row.get('error','') for text in ('HTTP 402','HTTP 401','HTTP 403','different model ID')):
                not_started=list(pending);pending.clear()
                write_json(OUTPUT/'stopped.json',{'at':utcnow(),'trigger':key,'not_started':not_started},exclusive=True)
            write_json(OUTPUT/'progress.json',{'completed':len(list((OUTPUT/'runs').glob('*/result.json'))),'planned_additional':len(selected)})
        if active:time.sleep(.25)
    rows=[load_json(OUTPUT/'runs'/s['run_key']/'result.json') for s in selected if (OUTPUT/'runs'/s['run_key']/'result.json').exists()]
    check(OUTPUT)
    write_json(OUTPUT/'results.json',{'at':utcnow(),'additional_batch':True,'planned':len(selected),'completed':len(rows)==len(selected),'not_started':not_started,'rows':rows},exclusive=True)
    print(json.dumps({'additional_runs':len(rows),'functional':sum(r['functional_passed'] for r in rows),'workflow':sum(r['workflow_passed'] for r in rows)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--with-safety',action='store_true');args=p.parse_args()
    recovery()
    if args.with_safety:
        from scripts.run_final_security import run
        run(SAFETY)
