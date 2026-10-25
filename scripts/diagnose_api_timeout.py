"""Frozen, no-retry API diagnostics. Never executes model tools or edits benchmarks."""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import random
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pico.config import load_project_env, provider_env

HOST = 'api.siliconflow.cn'
MODEL = 'zai-org/GLM-5.3-Flash'
DEFAULT_OUTPUT = 'artifacts/api-timeout-diagnosis-2026-10-25'


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value, exclusive=True):
    with Path(path).open('x' if exclusive else 'w', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def old_requests():
    """Read only completed public v2 fixtures; no user sessions or .env content."""
    rows = []
    for directory in sorted((ROOT/'artifacts/repair-regression-v2-2026-10-24/runs').iterdir()):
        events = [json.loads(l) for l in (directory/'model_io.jsonl').read_text(encoding='utf-8').splitlines()]
        requests = {e['index']: e for e in events if e['event']=='request'}
        for event in events:
            if event['event'] not in {'response', 'error'}:
                continue
            request = requests[event['index']]
            start = datetime.fromisoformat(request['at']).timestamp()
            end = datetime.fromisoformat(event['at']).timestamp()
            rows.append({'run':directory.name, 'index':event['index'], 'chars':len(request['prompt']),
                'utf8_bytes':len(request['prompt'].encode()), 'prompt_sha256':sha(request['prompt'].encode()),
                'source':(directory/'model_io.jsonl').relative_to(ROOT).as_posix(),
                'ok':event['event']=='response', 'elapsed_ms':event['elapsed_ms'],
                'started_at':request['at'], 'finished_at':event['at'], 'start':start, 'end':end,
                'usage':event.get('usage', {})})
    for row in rows:
        row['other_logical_requests_at_start'] = sum(other is not row and other['start']<=row['start']<other['end'] for other in rows)
    return rows


def synthetic_prompt(size):
    prefix = 'The following is inert padding data. Do not analyze it. After the data reply only OK.\n<data>\n'
    suffix = '\n</data>\nReply only OK.'
    filler = ('alpha beta gamma delta 0123456789\n' * (size//32+1))
    result = prefix + filler[:size-len(prefix)-len(suffix)] + suffix
    assert len(result)==size
    return result


def prepare(output):
    output.mkdir(parents=True, exist_ok=False)
    rows = old_requests()
    save(output/'historical_requests.json', rows)
    source = ROOT/'artifacts/repair-regression-v2-2026-10-24/runs/10-boltons_xfrange-r1/model_io.jsonl'
    archive = ROOT/'benchmarks/results/repair-regression-v2-2026-10-24/evidence.zip'
    assert sha(archive.read_bytes())=='88e4c110cb7314e0e270ff90b5e76014eac862a4b7ad9df2018ca039af186c0b'
    with zipfile.ZipFile(archive) as z:
        assert z.read(source.relative_to(ROOT).as_posix())==source.read_bytes()
    events = [json.loads(l) for l in source.read_text(encoding='utf-8').splitlines()]
    failed_index = next(e['index'] for e in events if e['event']=='error')
    real = next(e['prompt'] for e in events if e['event']=='request' and e['index']==failed_index)
    # Only the already-published frozen benchmark prompt is eligible for replay.
    loaded = load_project_env(ROOT)
    secrets = [v for k,v in loaded.items() if any(w in k.upper() for w in ('KEY','TOKEN','SECRET','PASSWORD')) and len(v)>=8]
    assert not any(secret in real for secret in secrets)
    cases = []
    groups = [(length,c) for length in (128,8192,32768) for c in (1,3)]
    random.Random(20261025).shuffle(groups)
    plan = []
    def case(kind, group, repeat, concurrency, prompt, stream=True):
        key = f'{len(cases)+1:02d}-{kind}-c{concurrency}-r{repeat}'
        payload = {'model':MODEL, 'messages':[{'role':'user','content':prompt}],
            'temperature':.2, 'max_tokens':64 if kind.startswith('padding') else 3072, 'stream':stream}
        if stream:
            payload['stream_options']={'include_usage':True}
        row = {'id':key, 'group':group, 'kind':kind, 'repeat':repeat, 'concurrency':concurrency,
            'stream':stream, 'socket_timeout_seconds':180, 'hard_deadline_seconds':120 if kind.startswith('padding') else 240,
            'payload':payload, 'prompt_chars':len(prompt), 'payload_utf8_bytes':len(json.dumps(payload).encode()),
            'prompt_sha256':sha(prompt.encode()), 'payload_sha256':sha(json.dumps(payload).encode())}
        if not stream:
            row['hard_deadline_seconds']=200
        cases.append(row)
        return key
    for length, concurrency in groups:
        group = f'padding-{length}-c{concurrency}'
        ids = [case('padding'+str(length),group,r,concurrency,synthetic_prompt(length)) for r in (1,2,3)]
        plan.append({'group':group, 'concurrency':concurrency, 'cases':ids, 'cooldown_seconds':0})
    for group, concurrency, repeats, stream in [('real-serial-before',1,[1],True),
            ('real-parallel',3,[1,2,3],True),('real-serial-after',1,[2,3],True),
            ('real-nonstream',1,[1],False)]:
        ids = [case('real',group,r,concurrency,real,stream) for r in repeats]
        plan.append({'group':group, 'concurrency':concurrency, 'cases':ids, 'cooldown_seconds':15})
    save(output/'cases.json', cases)
    save(output/'plan.json', plan)
    save(output/'protocol.json', {'created_at':utcnow(), 'endpoint':'https://'+HOST+'/v1/chat/completions',
        'model':MODEL, 'temperature':.2, 'transport_retries':0, 'agent_tools_executed':False,
        'scope':'Diagnostic transport requests, not repair evaluations or replacements. No reference solutions.',
        'real_source':source.relative_to(ROOT).as_posix(), 'real_source_request_index':failed_index,
        'real_prompt_sha256':sha(real.encode()), 'thinking_parameters':'not sent; provider defaults',
        'route':'Direct HTTPS, matching the configured urllib proxy bypass for api.siliconflow.cn',
        'limitations':['Client concurrency does not expose the provider account queue.',
            'Cancelled requests may continue server-side; cooldown does not prove cancellation.',
            'Repeated prompts may hit provider cache; usage is retained.',
            'Only one public failing prompt is replayed; results do not estimate Agent pass rates.']})
    files = ['cases.json','plan.json','protocol.json','historical_requests.json']
    save(output/'freeze.json', {'frozen_at':utcnow(), 'script_sha256':sha(Path(__file__).read_bytes()),
        'sha256':{name:sha((output/name).read_bytes()) for name in files}})
    print(json.dumps({'prepared':len(cases),'freeze_sha256':sha((output/'freeze.json').read_bytes())}), flush=True)


def check(output):
    freeze = read(output/'freeze.json')
    assert freeze['script_sha256']==sha(Path(__file__).read_bytes()), 'Diagnostic script changed'
    for name, expected in freeze['sha256'].items():
        assert sha((output/name).read_bytes())==expected, name


def worker(output, key):
    check(output)
    spec = next(c for c in read(output/'cases.json') if c['id']==key)
    directory = output/'runs'/key
    directory.mkdir(parents=True, exist_ok=False)
    load_project_env(ROOT)
    assert urllib.request.proxy_bypass(HOST) or not urllib.request.getproxies().get('https'), 'Direct probe would differ from application route'
    api_key = provider_env('PICO_OPENAI_API_KEY', ('OPENAI_API_KEY',))
    assert api_key, 'Missing API key'
    payload = json.dumps(spec['payload']).encode()
    assert sha(payload)==spec['payload_sha256']
    result = {k:v for k,v in spec.items() if k!='payload'}
    result.update(started_at=utcnow(), trace_id=uuid.uuid4().hex, ok=False, phase='start',
                  chunks=0, reasoning_chars=0, visible_chars=0, usage=None, body_bytes=0)
    start = time.monotonic()
    visible = []
    connection = None
    events = (directory/'events.jsonl').open('x', encoding='utf-8', newline='\n')
    def event(name, **values):
        record={'event':name, 'at':utcnow(), 'elapsed_ms':round((time.monotonic()-start)*1000), **values}
        events.write(json.dumps(record,ensure_ascii=False)+'\n');events.flush()
        if name not in result:
            result[name]=record['elapsed_ms']
        result['elapsed_ms']=record['elapsed_ms']
        save(directory/'progress.json', result, exclusive=False)
    try:
        event('worker_started')
        result['phase']='dns'
        addresses = socket.getaddrinfo(HOST,443,type=socket.SOCK_STREAM)
        event('dns_done', addresses=len(addresses))
        result['phase']='tcp'
        last_error = None
        for family, socktype, proto, _, address in addresses:
            raw = socket.socket(family,socktype,proto);raw.settimeout(15)
            try:
                raw.connect(address)
                break
            except OSError as exc:
                raw.close();last_error=exc
        else:
            raise last_error or OSError('No addresses')
        event('tcp_connected')
        result['phase']='tls'
        secure = ssl.create_default_context().wrap_socket(raw,server_hostname=HOST)
        secure.settimeout(spec['socket_timeout_seconds'])
        event('tls_connected')
        connection = http.client.HTTPSConnection(HOST, timeout=spec['socket_timeout_seconds'])
        connection.sock=secure
        result['phase']='send'
        connection.request('POST','/v1/chat/completions',body=payload,headers={
            'Authorization':'Bearer '+api_key, 'Content-Type':'application/json',
            'Accept':'text/event-stream' if spec['stream'] else 'application/json',
            'User-Agent':'CodeTrail-transport-diagnostic', 'X-Trace-Id':result['trace_id'], 'Connection':'close'})
        event('request_sent')
        result['phase']='headers'
        response = connection.getresponse()
        result['http_status']=response.status
        result['response_headers']={k:v for k,v in response.getheaders() if k.lower() in
            {'date','server','content-type','content-length','x-request-id','x-siliconcloud-trace-id','retry-after'} or 'ratelimit' in k.lower()}
        event('headers_received', status=response.status, headers=result['response_headers'])
        if response.status!=200:
            raise RuntimeError('HTTP '+str(response.status))
        result['phase']='body'
        if spec['stream']:
            last_progress = 0
            for line in response:
                result['body_bytes']+=len(line)
                if not result.get('first_byte'):
                    event('first_byte')
                if not line.startswith(b'data:'):
                    if line.startswith(b':'):
                        result['sse_comments']=result.get('sse_comments',0)+1
                    continue
                data = line[5:].strip()
                if data==b'[DONE]':
                    result['ok']=True;event('stream_done');break
                frame = json.loads(data)
                if frame.get('error'):
                    raise RuntimeError(str(frame['error']))
                result['chunks']+=1
                if frame.get('usage'):
                    result['usage']=frame['usage']
                choices = frame.get('choices') or []
                choice = choices[0] if choices else {}
                delta = choice.get('delta') or {}
                reasoning = delta.get('reasoning_content') or ''
                text = delta.get('content') or ''
                if reasoning and not result.get('first_reasoning'):
                    event('first_reasoning')
                if text and not result.get('first_visible'):
                    event('first_visible')
                result['reasoning_chars']+=len(reasoning)
                result['visible_chars']+=len(text)
                visible.append(text)
                if choice.get('finish_reason'):
                    result['finish_reason']=choice['finish_reason'];event('finish_reason_received',finish_reason=choice['finish_reason'])
                if time.monotonic()-last_progress>=2:
                    event('stream_progress',chunks=result['chunks'],reasoning_chars=result['reasoning_chars'],visible_chars=result['visible_chars'])
                    last_progress=time.monotonic()
        else:
            first=response.read1(1)
            result['body_bytes']+=len(first)
            if first:
                event('first_byte')
            body=first+response.read()
            result['body_bytes']=len(body)
            data=json.loads(body)
            result['usage']=data.get('usage')
            choice=(data.get('choices') or [{}])[0]
            message=choice.get('message') or {}
            text=message.get('content') or ''
            visible.append(text);result['visible_chars']=len(text)
            result['reasoning_chars']=len(message.get('reasoning_content') or '')
            result['finish_reason']=choice.get('finish_reason')
            if text:
                event('first_visible')
            result['ok']=bool(choice) and not data.get('error')
            event('body_complete')
        result['content']=''.join(visible)
        if not result['ok']:
            result['error']='Incomplete stream: EOF without [DONE]'
        event('finished',ok=result['ok'])
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc))
        event('failed', phase=result['phase'],error_type=type(exc).__name__)
    finally:
        if connection:
            connection.close()
        result['elapsed_ms']=round((time.monotonic()-start)*1000)
        result['finished_at']=utcnow()
        save(directory/'result.json', result)
        events.close()
    print(json.dumps({k:result.get(k) for k in ('id','ok','http_status','phase','first_reasoning','first_visible','reasoning_chars','elapsed_ms','error_type')},ensure_ascii=False), flush=True)


def run(output):
    check(output)
    save(output/'RUN_STARTED.json',{'at':utcnow(),'freeze_sha256':sha((output/'freeze.json').read_bytes())})
    specs={c['id']:c for c in read(output/'cases.json')}
    (output/'logs').mkdir()
    rows=[]
    for group in read(output/'plan.json'):
        pending=list(group['cases']);active={}
        print('GROUP '+group['group'],flush=True)
        while pending or active:
            while pending and len(active)<group['concurrency']:
                key=pending.pop(0);log=(output/'logs'/(key+'.log')).open('xb')
                process=subprocess.Popen([sys.executable,'-u','-X','utf8',str(Path(__file__).resolve()),
                    '--output',str(output),'--worker',key],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
                active[key]=(process,log,time.monotonic())
            for key,(process,log,started) in list(active.items()):
                deadline=time.monotonic()-started>specs[key]['hard_deadline_seconds']
                if process.poll() is None and not deadline:
                    continue
                if process.poll() is None:
                    process.terminate();process.wait(timeout=15)
                log.close();directory=output/'runs'/key;directory.mkdir(exist_ok=True,parents=True)
                if (directory/'result.json').exists():
                    row=read(directory/'result.json')
                else:
                    row=read(directory/'progress.json') if (directory/'progress.json').exists() else {k:v for k,v in specs[key].items() if k!='payload'}
                    row.update(ok=False, error_type='HardDeadline' if deadline else 'WorkerError',
                        error='Diagnostic deadline reached; partial telemetry retained' if deadline else 'Worker exited '+str(process.returncode),
                        finished_at=utcnow(),elapsed_ms=round((time.monotonic()-started)*1000))
                    save(directory/'deadline.json',row)
                rows.append(row)
                print(json.dumps({k:row.get(k) for k in ('id','ok','http_status','first_reasoning','first_visible','reasoning_chars','elapsed_ms','error_type')}),flush=True)
                del active[key]
                save(output/'progress.json',{'completed':len(rows),'planned':len(specs),'rows':rows},exclusive=False)
            if active:
                time.sleep(.2)
        if group['cooldown_seconds']:
            time.sleep(group['cooldown_seconds'])
    check(output)
    save(output/'results.json',{'finished_at':utcnow(),'completed':True,'rows':rows})
    print('ALL DIAGNOSTIC REQUESTS RETAINED: '+str(len(rows)),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=DEFAULT_OUTPUT)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--worker')
    args=parser.parse_args();output=(ROOT/args.output).resolve()
    if ROOT not in output.parents:
        parser.error('Output must be inside project')
    if args.prepare:
        prepare(output)
    elif args.worker:
        worker(output,args.worker)
    elif args.run:
        run(output)
    else:
        parser.print_help()


if __name__=='__main__':
    main()
