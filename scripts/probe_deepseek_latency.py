"""Replay one public prompt against official DeepSeek; no agent tools or retries."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.run_deepseek_validation import credentials
from pico.evaluation.repair_suite import load_json,write_json,append_json,digest,utcnow

DEFAULT_OUTPUT='artifacts/deepseek-latency-2026-10-25'


def prepare(output):
    source=ROOT/'artifacts/repair-regression-v2-2026-10-24/runs/10-boltons_xfrange-r1/model_io.jsonl'
    events=[json.loads(l) for l in source.read_text(encoding='utf-8').splitlines()]
    prompt=next(e['prompt'] for e in events if e['event']=='request' and e['index']==6)
    import hashlib
    assert hashlib.sha256(prompt.encode()).hexdigest()=='9071e5e6800db18a0ae3d087317d1d888743e26b1d08b8f8de95685e8b4538ad'
    output.mkdir(parents=True,exist_ok=False)
    plan=[]
    for group,concurrency,repeats,stream in [('serial-before',1,[1],True),('parallel',3,[1,2,3],True),
            ('serial-after',1,[2,3],True),('nonstream',1,[1],False)]:
        for repeat in repeats:
            plan.append({'id':f'{len(plan)+1:02d}-{group}-r{repeat}','group':group,'concurrency':concurrency,'stream':stream,
                'payload':{'model':'deepseek-flash','messages':[{'role':'user','content':prompt}],
                    'max_tokens':8192,'temperature':.2,'stream':stream,
                    **({'stream_options':{'include_usage':True}} if stream else {})}})
    write_json(output/'plan.json',plan,exclusive=True)
    write_json(output/'freeze.json',{'at':utcnow(),'plan_sha256':digest(output/'plan.json'),
        'script_sha256':digest(Path(__file__).resolve()),'endpoint':'https://api.deepseek.com/v1/chat/completions',
        'scope':'Seven transport requests only. Model/provider/thinking defaults differ from GLM; not provider-only causality.',
        'budget_boundary':'DeepSeek max_tokens8192 includes reasoning; SiliconFlow max_tokens3072 excluded reasoning. Nominal budgets are not equivalent.',
        'thinking':'No parameters sent: official default enabled, effort high; temperature is ignored in thinking mode.',
        'hard_deadline_seconds':240,'socket_timeout_seconds':180,'retries':0},exclusive=True)
    print(json.dumps({'prepared':7,'sha256':digest(output/'freeze.json')}),flush=True)


def check(output):
    freeze=load_json(output/'freeze.json')
    assert digest(output/'plan.json')==freeze['plan_sha256']
    assert digest(Path(__file__).resolve())==freeze['script_sha256']


def worker(output,key):
    check(output);cfg=credentials();spec=next(r for r in load_json(output/'plan.json') if r['id']==key)
    directory=output/'runs'/key;directory.mkdir(parents=True,exist_ok=False)
    row={k:v for k,v in spec.items() if k!='payload'}
    row.update(started_at=utcnow(),ok=False,phase='start',reasoning_chars=0,visible_chars=0,usage=None)
    start=time.monotonic();visible=[]
    def event(name,**values):
        row['elapsed_ms']=round((time.monotonic()-start)*1000)
        if name not in row:row[name]=row['elapsed_ms']
        append_json(directory/'events.jsonl',{'event':name,'at':utcnow(),'elapsed_ms':row['elapsed_ms'],**values})
        write_json(directory/'progress.json',row)
    request=urllib.request.Request('https://api.deepseek.com/v1/chat/completions',data=json.dumps(spec['payload']).encode(),
        headers={'Authorization':'Bearer '+cfg['PICO_DEEPSEEK_API_KEY'],'Content-Type':'application/json','User-Agent':'CodeTrail-transport-diagnostic'},method='POST')
    assert urllib.request.proxy_bypass('api.deepseek.com')
    try:
        row['phase']='headers';event('request_started')
        with urllib.request.urlopen(request,timeout=180) as response:
            row['http_status']=response.status
            row['response_headers']={k:v for k,v in response.headers.items() if k.lower() in
                {'date','content-type','x-request-id','x-ds-request-id','x-ds-trace-id'}}
            event('headers_received');row['phase']='body'
            if spec['stream']:
                last=0
                for line in response:
                    if 'first_byte' not in row:event('first_byte')
                    if not line.startswith(b'data:'):continue
                    data=line[5:].strip()
                    if data==b'[DONE]':row['ok']=True;event('stream_done');break
                    frame=json.loads(data)
                    if frame.get('error'):raise RuntimeError('Provider returned a stream error')
                    row['response_model']=frame.get('model',row.get('response_model'))
                    if frame.get('usage'):row['usage']=frame['usage']
                    choice=(frame.get('choices') or [{}])[0];delta=choice.get('delta') or {}
                    thought=delta.get('reasoning_content') or '';text=delta.get('content') or ''
                    if thought and 'first_reasoning' not in row:event('first_reasoning')
                    if text and 'first_visible' not in row:event('first_visible')
                    row['reasoning_chars']+=len(thought);row['visible_chars']+=len(text);visible.append(text)
                    if choice.get('finish_reason'):row['finish_reason']=choice['finish_reason']
                    if time.monotonic()-last>=2:event('progress',reasoning_chars=row['reasoning_chars'],visible_chars=row['visible_chars']);last=time.monotonic()
            else:
                first=response.read1(1)
                if first:event('first_byte')
                data=json.loads(first+response.read());choice=(data.get('choices') or [{}])[0];message=choice.get('message') or {}
                row['response_model']=data.get('model');row['usage']=data.get('usage');row['finish_reason']=choice.get('finish_reason')
                text=message.get('content') or '';visible.append(text);row['visible_chars']=len(text)
                row['reasoning_chars']=len(message.get('reasoning_content') or '')
                if text:event('first_visible')
                row['ok']=bool(choice) and not data.get('error')
        row['content']=''.join(visible);row['usable_visible_response']=bool(row['content'].strip()) and row.get('finish_reason')!='length'
        event('finished',ok=row['ok'])
    except Exception as exc:
        row.update(error_type=type(exc).__name__,error=('HTTP '+str(exc.code)) if isinstance(exc,urllib.error.HTTPError) else str(exc))
        event('failed',phase=row['phase'],error_type=row['error_type'])
    row.update(elapsed_ms=round((time.monotonic()-start)*1000),finished_at=utcnow())
    write_json(directory/'result.json',row,exclusive=True)
    print(json.dumps({k:row.get(k) for k in ('id','ok','elapsed_ms','first_reasoning','first_visible','finish_reason','error_type')}),flush=True)


def run(output):
    check(output);write_json(output/'started.json',{'at':utcnow()},exclusive=True)
    plan=load_json(output/'plan.json');rows=[];(output/'logs').mkdir()
    for group in ('serial-before','parallel','serial-after','nonstream'):
        pending=[r for r in plan if r['group']==group];active={}
        while pending or active:
            while pending and len(active)<(3 if group=='parallel' else 1):
                spec=pending.pop(0);log=(output/'logs'/(spec['id']+'.log')).open('xb')
                process=subprocess.Popen([sys.executable,'-u','-X','utf8',str(Path(__file__).resolve()),'--output',str(output),'--worker',spec['id']],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
                active[spec['id']]=(process,log,time.monotonic(),spec)
            for key,(process,log,start,spec) in list(active.items()):
                timeout=time.monotonic()-start>240
                if process.poll() is None and not timeout:continue
                if process.poll() is None:process.terminate();process.wait(timeout=15)
                log.close();directory=output/'runs'/key;directory.mkdir(parents=True,exist_ok=True)
                if (directory/'result.json').exists():row=load_json(directory/'result.json')
                else:
                    row=load_json(directory/'progress.json') if (directory/'progress.json').exists() else {k:v for k,v in spec.items() if k!='payload'}
                    row.update(ok=False,error_type='HardDeadline' if timeout else 'WorkerError',elapsed_ms=round((time.monotonic()-start)*1000))
                    write_json(directory/'deadline.json',row,exclusive=True)
                rows.append(row);del active[key];print(json.dumps({k:row.get(k) for k in ('id','ok','elapsed_ms','first_visible','error_type')}),flush=True)
            if active:time.sleep(.2)
    check(output);write_json(output/'results.json',{'completed':True,'at':utcnow(),'rows':rows},exclusive=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',default=DEFAULT_OUTPUT)
    p.add_argument('--prepare',action='store_true');p.add_argument('--run',action='store_true');p.add_argument('--worker')
    args=p.parse_args();output=(ROOT/args.output).resolve()
    if ROOT not in output.parents:p.error('Output must stay inside project')
    if args.prepare:prepare(output)
    elif args.worker:worker(output,args.worker)
    elif args.run:run(output)
    else:p.print_help()
