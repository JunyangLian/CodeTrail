"""Freeze, run, resume or inspect the fixed 20-task x 3 repair evaluation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import (SUITE, PROTOCOL, aggregate, check_freeze, digest,
    execute_run, freeze_suite, inventory, load_json, utcnow, write_json)

def controller(output, env_file, resume=False):
    freeze=check_freeze()
    plan=load_json(SUITE/'plan.json')
    if not resume:
        output.mkdir(parents=True,exist_ok=False)
        write_json(output/'batch.json',{'started_at':utcnow(),'freeze_sha256':digest(SUITE/'freeze.json'),
                    'protocol':PROTOCOL,'planned_runs':60,'unique_tasks':20,'completed':False},exclusive=True)
    batch=load_json(output/'batch.json')
    assert batch['freeze_sha256']==digest(SUITE/'freeze.json')
    pending=[]
    tasks={task['id']:task for task in load_json(SUITE/'tasks.json')['tasks']}
    for row in plan:
        directory=output/'runs'/row['run_key']
        if (directory/'result.json').exists():
            continue
        if directory.exists():
            # Resume may continue only unstarted slots. Interrupted slots remain failures.
            stopped={**row,'category':tasks[row['task_id']]['category'],'functional_passed':False,
                     'workflow_passed':False,'failure_category':'interrupted_or_worker_error',
                     'error':'previous worker interrupted; no replacement run', 'scope_valid':True}
            write_json(directory/'result.json',stopped,exclusive=True)
        else:
            pending.append(row)
    active={}
    while pending or active:
        while pending and len(active)<PROTOCOL['concurrency']:
            row=pending.pop(0)
            logs=output/'logs'
            logs.mkdir(exist_ok=True)
            stream=(logs/(row['run_key']+'.log')).open('wb')
            command=[sys.executable,'-u','-X','utf8',str(Path(__file__).resolve()),'--worker',row['run_key'],
                     '--output',str(output),'--env-file',str(env_file)]
            process=subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,
                                     env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
            active[row['run_key']]=(process,stream,time.monotonic(),row)
            print('START '+row['run_key'],flush=True)
        for key,(process,stream,started,row) in list(active.items()):
            deadline=time.monotonic()-started>PROTOCOL['run_deadline_seconds']
            if process.poll() is None and not deadline:
                continue
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            stream.close()
            directory=output/'runs'/key
            result=directory/'result.json'
            if not result.exists():
                directory.mkdir(parents=True,exist_ok=True)
                record={**row,'category':tasks[row['task_id']]['category'],'functional_passed':False,
                        'workflow_passed':False,'failure_category':'run_deadline' if deadline else 'worker_error',
                        'error':f'worker exit {process.returncode}; no rerun',
                        'controller_timeout':deadline,'scope_valid':True,'elapsed_ms':round((time.monotonic()-started)*1000)}
                write_json(result,record,exclusive=True)
            value=load_json(result)
            print(json.dumps({k:value.get(k) for k in ('run_key','functional_passed','workflow_passed','failure_category','tool_steps','elapsed_ms')}),flush=True)
            del active[key]
            rows=[load_json(path) for path in sorted((output/'runs').glob('*/result.json'))]
            write_json(output/'progress.json',{'completed_runs':len(rows),'planned_runs':60,'summary':aggregate(rows)})
        if active:
            time.sleep(.25)
    rows=[load_json(output/'runs'/row['run_key']/'result.json') for row in plan]
    assert len(rows)==60
    check_freeze()
    batch.update(completed=True,finished_at=utcnow(),summary=aggregate(rows),rows=rows)
    write_json(output/'results.json',batch,exclusive=True)
    print(json.dumps(batch['summary']),flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--worker')
    parser.add_argument('--output',default='artifacts/repair-suite-v1-2026-10-24')
    parser.add_argument('--env-file',default=str(ROOT/'.env'))
    args=parser.parse_args()
    output=(ROOT/args.output).resolve()
    if ROOT not in output.parents:
        parser.error('output must be within project')
    if args.freeze:
        freeze_suite()
    elif args.check:
        print(json.dumps({'freeze_sha256':digest(SUITE/'freeze.json'),'assets':len(check_freeze()['hashes'])}))
    elif args.worker:
        execute_run(output,args.worker,Path(args.env_file).resolve())
    else:
        controller(output,Path(args.env_file).resolve(),args.resume)

if __name__=='__main__':
    main()
