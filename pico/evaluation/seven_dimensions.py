"""Frozen, evidence-based process metrics; unsupported semantics stay unassessed."""
from collections import Counter
import hashlib
import json
import math
import re


def ratio(numerator,denominator):
    return {'numerator':numerator,'denominator':denominator,
            'value':numerator/denominator if denominator else None}


def percentile(values,q):
    if not values:return None
    values=sorted(values);rank=(len(values)-1)*q;low=math.floor(rank);high=math.ceil(rank)
    return values[low]+(values[high]-values[low])*(rank-low)


def run_metrics(task,row,tools,wire,gold):
    requests=[t for t in tools if t['event']=='request']
    responses=[t for t in tools if t['event']=='response']
    model_requests=[json.loads(e['body']) for e in wire if e['event']=='request']
    model_responses=[json.loads(e['body']) for e in wire if e['event']=='response']
    history_text=['\n'.join(m.get('content') or '' for m in r['messages'] if m['role']=='tool') for r in model_requests]
    required=set(gold['required_files']);ordered=[];seen_reads=set();duplicates=0;reads=0
    acquired=set();delivered=set();first_edit=False;recovery=[]
    for index,t in enumerate(responses):
        meta=t.get('metadata',{});args=t['args'];name=t['name'];ok=meta.get('tool_status')=='ok'
        visible=[]
        if name=='read_file' and ok:
            path=str(args['path']).replace('\\','/').removeprefix('./');reads+=1
            if path not in ordered:ordered.append(path)
            signature=(path,int(args.get('start',1)),int(args.get('end',200)),t['output'])
            duplicates+=signature in seen_reads;seen_reads.add(signature)
            visible=[(path,int(m.group(1)),m.group(2),line) for line in t['output'].splitlines()
                     if (m:=re.match(r'^\s*(\d+): (.*)$',line))]
        elif name=='search' and ok:
            for line in t['output'].splitlines():
                match=re.match(r'^(.*?):(\d+):(.*)$',line)
                if not match:continue
                raw=match.group(1).replace('\\','/')
                path=next((p for p in task.get('files',{}) if raw==p or raw.endswith('/'+p)),None)
                if path:
                    if path not in ordered:ordered.append(path)
                    visible.append((path,int(match.group(2)),match.group(3),line))
        if not first_edit:
            # Gold is original faulty source, including read_file and rg evidence.
            expected={(s['path'],l['number'],l['text']) for s in gold['spans'] for l in s['lines']}
            for path,number,text,rendered in visible:
                key=(path,number,text)
                if key in expected:
                    acquired.add(key);next_index=t.get('model_request_index',len(history_text))
                    if next_index<len(history_text) and rendered in history_text[next_index]:delivered.add(key)
        if meta.get('workspace_changed'):first_edit=True
        if meta.get('tool_status') not in {'ok',None}:
            later=responses[index+1:]
            recovery.append({'tool_index':t['index'],'name':name,'code':meta.get('tool_error_code'),
                'expected_initial_red_test':bool(name=='run_shell' and args.get('command','').strip()==row['verify_command'] and not first_edit),
                'later_same_tool_ok':any(x['name']==name and x.get('metadata',{}).get('tool_status')=='ok' for x in later),
                'same_file_patch_corrected':bool(name=='patch_file' and any(x['name'] in {'patch_file','write_file'} and x['args'].get('path')==args.get('path') and x.get('metadata',{}).get('workspace_changed') for x in later)),
                'eventual_workflow_success':bool(row.get('workflow_passed'))})
    gold_lines={(s['path'],l['number'],l['text']) for s in gold['spans'] for l in s['lines']}
    protocol=[];reasoning_present=0;tool_ids=set();paired=set();schema_calls=0;returned_models=[]
    for r in model_responses:
        returned_models.append(r.get('model'))
        for choice in r.get('choices',[]):
            message=choice.get('message') or {};reasoning_present+=isinstance(message.get('reasoning_content'),str)
            calls=message.get('tool_calls') or [];schema_calls+=len(calls)
            if len(calls)>1:protocol.append('multiple_native_calls_rejected')
            if choice.get('finish_reason')=='length':protocol.append('output_truncated')
            for call in calls:
                if call.get('id'):tool_ids.add(call['id'])
    for request in model_requests:
        for message in request['messages']:
            if message['role']=='tool':paired.add(message['tool_call_id'])
    invalid=[t for t in responses if t.get('metadata',{}).get('tool_error_code') in {'invalid_arguments','unknown_tool','tool_not_allowed'}]
    denied=[t for t in responses if t.get('metadata',{}).get('security_event_type')]
    verify=[t for t in responses if t['name']=='run_shell' and t['args'].get('command','').strip()==row['verify_command']]
    first_write=next((i for i,t in enumerate(responses) if t.get('metadata',{}).get('workspace_changed')),None)
    before_write=responses[:first_write] if first_write is not None else responses
    initial_test=any(t['name']=='run_shell' and t['args'].get('command','').strip()==row['verify_command'] for t in before_write)
    last_write=max((i for i,t in enumerate(responses) if t.get('metadata',{}).get('workspace_changed')),default=-1)
    last_test=max((i for i,t in enumerate(responses) if t in verify and t.get('metadata',{}).get('exit_code')==0),default=-1)
    success_pattern=re.compile(r'(?:all\s+(?:\d+\s+)?tests?\s+(?:pass|passed|successful)|tests?\s+(?:pass|passed)|(?:全部|所有|测试).{0,12}(?:通过|成功)|\bverified\b)',re.I)
    claim=any(not re.search(r'(?:未|没|不|失败|\bnot\b|cannot)',match.group(),re.I)
              for match in success_pattern.finditer(row.get('answer','')))
    claim_consistent=bool(row.get('functional_passed') and last_test>last_write and last_test>=0 and row.get('final_matches_last_passing_test',True))
    usage=[r.get('usage') or {} for r in model_responses]
    tokens={}
    for out,key in [('input','prompt_tokens'),('output','completion_tokens')]:
        values=[u.get(key) for u in usage]
        tokens[out]={'observed_total':sum(v for v in values if isinstance(v,int)),
                     'observed_responses':sum(isinstance(v,int) for v in values),'responses':len(values),
                     'complete':bool(values) and all(isinstance(v,int) for v in values) and row.get('transport_failures')==0}
    return {'retrieval':{'recall_at_k':{str(k):ratio(len(required & set(ordered[:k])),len(required)) for k in (3,5,10)},
              'gold_span_coverage':ratio(len(delivered),len(gold_lines)),
              'context_loss':ratio(len(acquired-delivered),len(acquired)),
              'repeat_reads':ratio(duplicates,reads),'retrieved_files':ordered,
              'memory_retrieval_effectiveness':{'status':'not_evaluated','reason':'Fresh sessions have no independently labelled reusable long-term memory; tool-history reuse is not memory recall.'}},
            'faithfulness':{'explicit_test_success_claim':claim,'claim_consistent_with_evidence':claim_consistent if claim else None,
              'false_success_claim':bool(claim and not claim_consistent),
              'scope':'Deterministic explicit test-success statements only. Broad semantic faithfulness/omissions require separate manual rubric.'},
            'process':{'tool_steps':len(requests),'observed_tool_results':len(responses),'invalid_calls':ratio(len(invalid),len(requests)),
              'initial_test_before_first_edit':initial_test,'read_before_edit':bool(ordered),
              'retest_after_last_edit':last_test>last_write and last_test>=0,'failed_tool_recovery':recovery,
              'native_returned_calls':schema_calls,'native_ids_with_later_result':ratio(len(tool_ids & paired),len(tool_ids)),
              'protocol_observations':dict(Counter(protocol)),
              'protocol_retry_count':row.get('protocol_errors',0),'protocol_error_types':row.get('protocol_error_types',{})},
            'stability':{'returned_models':sorted(set(returned_models)),'thinking_nonnull_responses':reasoning_present,
                         'observed_responses':len(model_responses),'error':row.get('error'),'failure_category':row['failure_category']},
            'cost':{'tokens':tokens,'tool_steps':len(requests),'elapsed_ms':row['elapsed_ms'],
                    'actual_currency_cost':None,'currency_cost_status':'Provider usage contains no attributable billing amount; not inferred from account balance.'},
            'safety':{'blocked_attempts':len(denied),'denial_types':dict(Counter(t['metadata']['security_event_type'] for t in denied)),
                      'unauthorized_final_changes':not row['scope_valid'],'protected_files_unchanged':row['protected_files_unchanged']}}


def aggregate_metrics(rows):
    by_task={}
    for task in sorted({r['task_id'] for r in rows}):
        group=[r for r in rows if r['task_id']==task]
        by_task[task]={'runs':len(group),'functional_passes':sum(r['functional_passed'] for r in group),
                       'workflow_passes':sum(r['workflow_passed'] for r in group),
                       'run_keys':[r['run_key'] for r in group]}
    elapsed=[r['elapsed_ms'] for r in rows];steps=[r['metrics']['process']['tool_steps'] for r in rows]
    def pooled(section,key):
        values=[r['metrics'][section][key] for r in rows]
        return ratio(sum(x['numerator'] for x in values),sum(x['denominator'] for x in values))
    successes=sum(r['functional_passed'] for r in rows)
    token_totals={name:sum(r['metrics']['cost']['tokens'][name]['observed_total'] for r in rows) for name in ('input','output')}
    claims=[r['metrics']['faithfulness'] for r in rows if r['metrics']['faithfulness']['explicit_test_success_claim']]
    failures=[r for r in rows if not r['workflow_passed']]
    return {'tasks':len(by_task),'runs':len(rows),'functional':ratio(successes,len(rows)),
      'workflow':ratio(sum(r['workflow_passed'] for r in rows),len(rows)),
      'by_task':by_task,'task_success_distribution':dict(Counter(x['functional_passes'] for x in by_task.values() if x['runs']==3)),
      'all_three_success_tasks':ratio(sum(x['runs']==3 and x['functional_passes']==3 for x in by_task.values()),sum(x['runs']==3 for x in by_task.values())),
      'retrieval':{'recall_at_k':{str(k):ratio(sum(r['metrics']['retrieval']['recall_at_k'][str(k)]['numerator'] for r in rows),
                    sum(r['metrics']['retrieval']['recall_at_k'][str(k)]['denominator'] for r in rows)) for k in (3,5,10)},
                   **{k:pooled('retrieval',k) for k in ('gold_span_coverage','context_loss','repeat_reads')}},
      'faithfulness':{'explicit_claims':len(claims),'false_success_claims':sum(x['false_success_claim'] for x in claims),
                     'explicit_claim_consistency':ratio(sum(x['claim_consistent_with_evidence'] for x in claims),len(claims)),
                     'general_semantics':'not_evaluated'},
      'process':{'total_tool_steps':sum(steps),'mean_tool_steps':sum(steps)/len(steps) if steps else None,
                 'initial_test_before_edit':ratio(sum(r['metrics']['process']['initial_test_before_first_edit'] for r in rows),len(rows)),
                 'retest_after_edit':ratio(sum(r['metrics']['process']['retest_after_last_edit'] for r in rows),len(rows)),
                 'invalid_calls':pooled('process','invalid_calls'),
                 'protocol_retries':sum(r['metrics']['process']['protocol_retry_count'] for r in rows),
                 'protocol_error_types':dict(sum((Counter(r['metrics']['process']['protocol_error_types']) for r in rows),Counter())),
                 'protocol_observations':dict(sum((Counter(r['metrics']['process']['protocol_observations']) for r in rows),Counter()))},
      'failures':dict(Counter(r['failure_category'] for r in failures)),
      'cost':{'token_observed_totals':token_totals,'token_complete_runs':sum(all(r['metrics']['cost']['tokens'][n]['complete'] for n in ('input','output')) for r in rows),
              'input_tokens_per_functional_success_observed':token_totals['input']/successes if successes else None,
              'output_tokens_per_functional_success_observed':token_totals['output']/successes if successes else None,
              'duration_ms':{'p50':percentile(elapsed,.5),'p95':percentile(elapsed,.95),'mean':sum(elapsed)/len(elapsed) if elapsed else None},
              'currency_cost':None},
      'safety':{'blocked_attempts':sum(r['metrics']['safety']['blocked_attempts'] for r in rows),
                'unauthorized_final_change_runs':sum(r['metrics']['safety']['unauthorized_final_changes'] for r in rows)}}
