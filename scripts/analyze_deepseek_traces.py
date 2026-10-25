"""Offline, deterministic metrics for retained DeepSeek repair traces; no model calls."""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import digest,load_json,write_json,utcnow

BATCHES=['deepseek-validation-2026-10-25','deepseek-validation-dsml-2026-10-25','deepseek-validation-thinking-2026-10-25']


def events(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def analyze(batch):
    results=load_json(batch/'results.json');rows=[]
    assert len(results['rows'])==12
    for result in results['rows']:
        directory=batch/'runs'/result['run_key']
        assert load_json(directory/'result.json')==result
        trace=ROOT/result['trace'];assert digest(trace)==result['trace_sha256']
        ts=events(trace);io=events(directory/'model_io.jsonl');tools=events(directory/'tool_io.jsonl')
        seen=set();duplicates=[];reads=[]
        for index,t in enumerate(tools):
            if t['name']!='read_file':continue
            args=t['args'];normalized=(str(Path(args['path'])).replace('\\','/'),int(args.get('start',1)),int(args.get('end',200)))
            key=(normalized,t['output'])
            if key in seen:duplicates.append({'tool_io_line':index+1,'args':args,'output_sha256':hashlib.sha256(t['output'].encode()).hexdigest()})
            seen.add(key);reads.append(t)
        executed=[e for e in ts if e['event']=='tool_executed']
        retries=[e for e in ts if e['event']=='model_parsed' and e['kind']=='retry']
        responses=[e for e in io if e['event']=='response']
        requests=[e for e in io if e['event']=='request']
        markers=[]
        for index,t in enumerate(executed):
            if t['name'] in {'patch_file','write_file'} and re.search(r'[｜|]+DSML',json.dumps(t['args'],ensure_ascii=False)):
                markers.append({'tool_step':index+1,'name':t['name'],'status':t.get('tool_status'),'args':t['args']})
        failures=[{'tool_step':i+1,'name':e['name'],'args':e['args'],'code':e.get('tool_error_code'),'result':e.get('result')}
                  for i,e in enumerate(executed) if e.get('tool_error_code')]
        # Inspect next effective request for each recorded successful read. Exact
        # numbered source lines absent from it are an observable delivery loss.
        delivery=[];cursor=0;request_index=0
        for event in ts:
            if event['event']=='model_parsed':request_index+=1
            if event['event']!='tool_executed':continue
            candidate=next((i for i in range(cursor,len(tools)) if tools[i]['name']==event['name'] and tools[i]['args']==event['args']),None)
            if candidate is None or event.get('tool_status')=='rejected':continue
            t=tools[candidate];cursor=candidate+1
            if t['name']!='read_file' or request_index>=len(requests):continue
            request=requests[request_index]
            if 'native_request' in request:
                content='\n'.join(m.get('content') or '' for m in request['native_request']['messages'] if m['role']=='tool')
            else:content=request['prompt']
            source_lines=[l for l in t['output'].splitlines() if re.match(r'\s*\d+:',l)]
            missing=[l for l in source_lines if l not in content]
            delivery.append({'tool_io_line':candidate+1,'next_model_index':request['index'],'numbered_lines':len(source_lines),
                             'missing_lines':len(missing),'first_missing_line':missing[0] if missing else None})
        row={k:result.get(k) for k in ('run_key','task_id','repeat','functional_passed','workflow_passed','status','stop_reason',
            'failure_category','elapsed_ms','input_tokens','output_tokens','transport_failures','changed_files','scope_valid','protected_files_unchanged')}
        row.update(tool_steps=len(executed),executed_reads=len(reads),duplicate_reads=len(duplicates),duplicate_read_evidence=duplicates,
            protocol_errors=len(retries),tool_errors=dict(Counter(f['code'] for f in failures)),tool_failure_evidence=failures,
            mixed_marker_edits=markers,read_delivery=delivery,reads_with_missing_lines=sum(d['missing_lines']>0 for d in delivery),
            missing_numbered_lines=sum(d['missing_lines'] for d in delivery),
            native_calls=sum(r.get('usage',{}).get('native_tool_calls',0) for r in responses),
            reasoning_replay_turns=sum(r.get('usage',{}).get('replayed_assistant_turns',0) for r in responses),
            evidence={'model_io':str((directory/'model_io.jsonl').relative_to(ROOT)),
                      'tool_io':str((directory/'tool_io.jsonl').relative_to(ROOT)),'trace':result['trace'],
                      'changes':str((directory/'changes.diff').relative_to(ROOT))})
        rows.append(row)
    summary={key:sum(r[key] for r in rows) for key in ('tool_steps','executed_reads','duplicate_reads','protocol_errors','reads_with_missing_lines','missing_numbered_lines','native_calls','reasoning_replay_turns')}
    summary.update(functional=sum(r['functional_passed'] for r in rows),workflow=sum(r['workflow_passed'] for r in rows),
        mean_tool_steps=summary['tool_steps']/12,mixed_marker_edit_calls=sum(len(r['mixed_marker_edits']) for r in rows),
        input_tokens=sum(r['input_tokens']['observed_total'] for r in rows),output_tokens=sum(r['output_tokens']['observed_total'] for r in rows),
        elapsed_ms=sum(r['elapsed_ms'] for r in rows),tool_errors=dict(sum((Counter(r['tool_errors']) for r in rows),Counter())))
    return {'batch':str(batch.relative_to(ROOT)),'freeze_sha256':digest(batch/'freeze.json'),
            'results_sha256':digest(batch/'results.json'),'summary':summary,'rows':rows}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--include')
    args=parser.parse_args();names=BATCHES+([args.include] if args.include else [])
    report={'at':utcnow(),'scope':'6 independent tasks x 2 repetitions per cohort; repeated runs are not new tasks',
        'definitions':{'duplicate_reads':'Same normalized path/start/end and identical successful read output previously in the run; beyond first; rejected attempts excluded.',
          'protocol_errors':'model_parsed.kind=retry; does not include rejected tool arguments or failed assertions.',
          'missing_lines':'Numbered source line absent from the next effective request. A line may remain available in older native tool messages. Reasoning bodies are not logged.',
          'causal_limit':'Observational root-cause evidence; bundled protocol/context/feedback fix cannot isolate individual causal effects.'},
        'cohorts':[analyze(ROOT/'artifacts'/name) for name in names]}
    write_json(ROOT/args.output,report,exclusive=True)
    print(json.dumps([{'batch':c['batch'],**c['summary']} for c in report['cohorts']],ensure_ascii=False))
