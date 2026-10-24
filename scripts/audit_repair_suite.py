"""Recheck all 60 slots using frozen tests; do not modify any original run record."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from datetime import datetime
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import (SUITE, PROTOCOL, aggregate, check_freeze, digest,
    inventory, load_json, utcnow, verify_pristine, write_json)

def events_at(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

def observed_usage(calls):
    requests=[call for call in calls if call['event']=='request']
    responses=[call for call in calls if call['event']=='response']
    result={}
    for field in ('input_tokens','output_tokens'):
        values=[call.get('usage',{}).get(field) for call in responses]
        result[field]={'observed_total':sum(v for v in values if isinstance(v,int)),
                       'observed_calls':sum(isinstance(v,int) for v in values),
                       'successful_calls':len(responses),
                       'complete':bool(responses) and all(isinstance(v,int) for v in values) and len(requests)==len(responses)}
    return result

def audit(output, destination):
    check_freeze()
    plan=load_json(SUITE/'plan.json')
    tasks={task['id']:task for task in load_json(SUITE/'tasks.json')['tasks']}
    batch=load_json(output/'results.json')
    assert batch['completed'] and len(batch['rows'])==60
    assert batch['freeze_sha256']==digest(SUITE/'freeze.json')
    planned={(row['task_id'],row['repeat']) for row in plan}
    actual={(row['task_id'],row['repeat']) for row in batch['rows']}
    assert len(planned)==len(actual)==len(batch['rows'])==60 and planned==actual
    assert Counter(row['task_id'] for row in plan)=={task:3 for task in tasks}
    destination.mkdir(parents=True,exist_ok=False)
    rows=[]
    session_ids=[]
    source_hashes={}
    for index,slot in enumerate(plan,1):
        key=slot['run_key'];task=tasks[slot['task_id']];directory=output/'runs'/key
        record=load_json(directory/'result.json')
        assert (record['task_id'],record['repeat'])==(slot['task_id'],slot['repeat'])
        original=next(row for row in batch['rows'] if row['run_key']==key)
        assert original==record
        workspace=directory/'workspace'
        final=inventory(workspace)
        if 'final_sha256' in record:
            assert final==record['final_sha256'],key
            assert record['initial_sha256']==task['sha256'],key
        changes=[name for name in sorted(set(task['files'])|set(final)) if task['sha256'].get(name)!=final.get(name)]
        scope=not (set(changes)-set(task['editable_files'])) and all(final.get(name)==value for name,value in task['sha256'].items() if name not in task['editable_files'])
        implementations={name:(workspace/name).read_bytes() for name in task['editable_files'] if (workspace/name).is_file()}
        verifier=verify_pristine(task,implementations,destination/'rechecks'/key)
        functional=bool(scope and len(implementations)==len(task['editable_files']) and verifier['accepted'])
        trace=ROOT/record['trace'] if record.get('trace') else None
        events=events_at(trace) if trace else []
        if trace:
            assert digest(trace)==record['trace_sha256'],key
        tools=[(number+1,event) for number,event in enumerate(events) if event['event']=='tool_executed']
        final_write=max((number for number,event in tools if event.get('workspace_changed')),default=-1)
        retests=[number for number,event in tools if event.get('name')=='run_shell'
                 and event.get('args',{}).get('command','').strip()==PROTOCOL['verify_command']
                 and event.get('exit_code')==0 and number>final_write]
        workflow=bool(functional and retests and record.get('status')=='completed' and not record.get('error'))
        if 'external_verifier' in record:
            assert functional==record['functional_passed'] and workflow==record['workflow_passed'],key
            assert verifier['tests_run']==record['external_verifier']['tests_run'],key
            assert verifier['exit_code']==record['external_verifier']['exit_code'],key
        calls=events_at(directory/'model_io.jsonl')
        requests=[call for call in calls if call['event']=='request']
        responses=[call for call in calls if call['event']=='response']
        errors=[call for call in calls if call['event']=='error']
        assert len({call['index'] for call in requests})==len(requests)
        dangling=len(requests)-len(responses)-len(errors)
        assert dangling>=0 and (dangling==0 or record.get('error')),key
        for call in requests:
            prompt=call['prompt']
            assert not any(marker in prompt for marker in ('reference_solutions.json','buggy_commit','fix_commit','BEGIN REFERENCE SOLUTION'))
            # Fresh contextual root must stay inside its own fixture, including refreshes.
            context_root=prompt.split('- repo_root: ',1)[1].splitlines()[0].strip()
            assert Path(context_root).resolve()==workspace.resolve(),key
        parsed=[event for event in events if event['event']=='model_parsed']
        assert len(parsed)==len(responses),key
        input_usage=[call.get('usage',{}).get('input_tokens') for call in responses]
        output_usage=[call.get('usage',{}).get('output_tokens') for call in responses]
        input_tokens={'observed_total':sum(v for v in input_usage if isinstance(v,int)), 'observed_calls':sum(isinstance(v,int) for v in input_usage),
                      'successful_calls':len(responses),'complete':bool(responses) and all(isinstance(v,int) for v in input_usage)}
        output_tokens={'observed_total':sum(v for v in output_usage if isinstance(v,int)), 'observed_calls':sum(isinstance(v,int) for v in output_usage),
                       'successful_calls':len(responses),'complete':bool(responses) and all(isinstance(v,int) for v in output_usage)}
        if 'input_tokens' in record:
            assert input_tokens==record['input_tokens'] and output_tokens==record['output_tokens'],key
        # Raw harness flags cover successful replies only. The audited run-level
        # coverage also requires usage for every attempted request, including failures.
        strict=observed_usage(calls)
        input_tokens=strict['input_tokens']
        output_tokens=strict['output_tokens']
        issues=[{'trace_line':number,'tool':event.get('name'),'code':event.get('tool_error_code'),'args':event.get('args')} for number,event in tools if event.get('tool_status')!='ok']
        blocked=[{'trace_line':number,'tool':event.get('name'),'type':event.get('security_event_type')} for number,event in tools if event.get('security_event_type')]
        cause=('success' if workflow else 'run_deadline' if record.get('controller_timeout') else 'model_or_runtime_error' if record.get('error') else
               'protected_file_or_scope_violation' if not scope else 'no_code_change' if not changes else
               'test_regression_or_incomplete_fix' if not functional else 'missing_reverification_or_incomplete_workflow')
        subcategory=('success' if workflow else 'provider_request_timeout' if 'timed out' in record.get('error','').lower() else
                     'premature_final_without_code_change' if not changes and record.get('status')=='completed' else
                     'tool_budget_exhausted' if record.get('stop_reason')=='step_limit_reached' else
                     'missing_post_edit_test' if functional and not retests else cause)
        details=[]
        if any(issue['code']=='invalid_arguments' for issue in issues): details.append('invalid_tool_arguments')
        if any(issue['code']=='repeated_identical_call' for issue in issues): details.append('repeated_tool_calls')
        if record.get('stop_reason')=='step_limit_reached': details.append('tool_budget_exhausted')
        if functional and not retests: details.append('no_successful_test_after_last_write')
        if subcategory=='premature_final_without_code_change':
            details.append('Model returned ordinary final text before repairing code; completed runtime state rejected by independent tests: '+record.get('answer',''))
        if record.get('error'): details.append(record['error'])
        if not functional and verifier.get('output'): details.append(verifier['output'])
        row={**slot,'category':task['category'],'origin_kind':task['origin']['kind'],
             'functional_passed':functional,'workflow_passed':workflow,'failure_category':cause,'failure_subcategory':subcategory,
             'unmatched_request_count':dangling,'failed_model_calls':len(errors),
             'failure_evidence':details,'tool_steps':len(tools),'attempts':len(requests),
             'input_tokens':input_tokens,'output_tokens':output_tokens,'elapsed_ms':record.get('elapsed_ms'),
             'agent_elapsed_ms':record.get('agent_elapsed_ms'),'protected_files_unchanged':scope,'scope_valid':scope,
             'changed_files':changes,'security_events':blocked,'tool_issues':issues,'external_recheck':verifier,
             'trace':record.get('trace'),'trace_sha256':record.get('trace_sha256'),
             'raw_record':(directory/'result.json').relative_to(ROOT).as_posix(),'raw_record_sha256':digest(directory/'result.json'),
             'diff':(directory/'changes.diff').relative_to(ROOT).as_posix()}
        rows.append(row)
        if record.get('session_id'): session_ids.append(record['session_id'])
        # Hash all raw evidence including sessions, traces, tool/model I/O and final code.
        for path in sorted(directory.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                source_hashes[path.relative_to(ROOT).as_posix()]=digest(path)
        print(json.dumps({'audited':index,'run_key':key,'functional':functional,'workflow':workflow,'cause':cause}),flush=True)
    assert len(session_ids)==len(set(session_ids))
    check_freeze()
    summary=aggregate(rows)
    summary['failure_subcategories']=dict(Counter(row['failure_subcategory'] for row in rows if not row['workflow_passed']))
    summary['model_requests']=sum(row['attempts'] for row in rows)
    summary['failed_model_requests']=sum(row['failed_model_calls']+row['unmatched_request_count'] for row in rows)
    summary['tool_issue_counts']=dict(Counter(issue['code'] for row in rows for issue in row['tool_issues']))
    summary['security_event_types']=dict(Counter(event['type'] for row in rows for event in row['security_events']))
    summary['per_origin']={kind:{'unique_tasks':len({row['task_id'] for row in subset}),'runs':len(subset),
                                'functional_passes':sum(row['functional_passed'] for row in subset),
                                'workflow_passes':sum(row['workflow_passed'] for row in subset)}
                           for kind in sorted({row['origin_kind'] for row in rows})
                           if (subset:=[row for row in rows if row['origin_kind']==kind])}
    summary['batch_wall_seconds']=(datetime.fromisoformat(batch['finished_at'])-datetime.fromisoformat(batch['started_at'])).total_seconds()
    report={'schema_version':1,'audited_at':utcnow(),'source_results':(output/'results.json').relative_to(ROOT).as_posix(),
            'auditor_sha256':digest(Path(__file__).resolve()),
            'source_results_sha256':digest(output/'results.json'),'freeze_sha256':digest(SUITE/'freeze.json'),
            'summary':summary,'rows':rows,'evidence_sha256':source_hashes,
            'token_coverage_note':'Raw harness complete flags cover successfully parsed model replies only; audited complete flags additionally require usage for every attempted logical request. Original records are retained unchanged.',
            'environment':{'GIT_CEILING_DIRECTORIES':'set to project artifacts directory before controller launch; audited every prompt root',
                           'python':sys.version,'executable':sys.executable},
            'boundaries':['20 unique tasks, 60 dependent repeated runs; do not treat as 60 independent tasks.',
                '12 author-created tasks, including 2 previously used; 8 extracted real upstream faults from only 2 projects.',
                'Visible acceptance tests and minimal fixtures; no hidden holdout or full repository benchmark.',
                'External tests use fresh frozen copies; reference code never installed in an agent fixture.',
                'Tool approval and file hashes are engineering guards, not an OS-level sandbox.',
                'Provider may be nondeterministic; replay of saved evidence is exact, new model runs need not reproduce the score.',
                'Token totals are provider-reported observations, excluding unknown usage of transport failures; no fabricated zeroes.']}
    write_json(destination/'audit.json',report,exclusive=True)
    write_report(destination/'report.md',report)
    print(json.dumps(report['summary']),flush=True)
    return report

def write_report(path,report):
    s=report['summary']; rows=report['rows']
    lines=['# CodeTrail 真实代码修复评测（20题 × 3次）','',
           f"冻结指纹：`{report['freeze_sha256']}`。独立任务 {s['unique_tasks']} 个，运行 {s['runs']} 次；每题3次，所有计划运行均纳入。",'',
           f"功能验收：**{s['functional_passes']}/{s['runs']}（{s['functional_rate']:.2%}）**。完整流程：**{s['workflow_passes']}/{s['runs']}（{s['workflow_rate']:.2%}）**。",'',
           '功能验收要求冻结测试在独立干净目录通过、测试及非授权文件不变。完整流程额外要求Agent正常结束，最后一次修改后指定测试命令成功。',
           '', '| 题型 | 独立任务 | 运行 | 功能通过 | 完整流程通过 |','|---|---:|---:|---:|---:|']
    labels={'single_file':'单文件逻辑','cross_file':'跨文件契约','unknown_location':'未知位置定位','regression_compatibility':'回归与兼容'}
    for category,stats in s['per_category'].items():
        lines.append(f"| {labels[category]} | {PROTOCOL['repetitions'] and stats['runs']//3} | {stats['runs']} | {stats['functional_passes']} | {stats['workflow_passes']} |")
    lines+=['','| 来源 | 独立任务 | 运行 | 功能通过 | 完整流程通过 |','|---|---:|---:|---:|---:|']
    for origin,stats in s['per_origin'].items():
        label='自行设计' if origin=='author_created' else '真实上游故障最小复现'
        lines.append(f"| {label} | {stats['unique_tasks']} | {stats['runs']} | {stats['functional_passes']} | {stats['workflow_passes']} |")
    lines+=['','## 每题结果（运行次序遵循冻结计划）','','| 任务 | 功能通过/3 | 流程通过/3 |','|---|---:|---:|']
    for task,stats in s['per_task'].items():
        lines.append(f"| {task} | {stats['functional_passes']}/3 | {stats['workflow_passes']}/3 |")
    lines+=['','## 过程开销与安全','',
            f"- 工具调用总数 {s['tool_steps']['total']}，平均 {s['tool_steps']['mean']:.2f} 次/运行（含拒绝与失败调用）。",
            f"- 运行总耗时 {s['elapsed_ms']['total']/1000:.3f} 秒，平均 {s['elapsed_ms']['mean']/1000:.3f} 秒；并发为3，批次墙钟时间 {s['batch_wall_seconds']:.3f} 秒。",
            f"- 可观测输入Token {s['input_tokens_observed']}，输出Token {s['output_tokens_observed']}；完整用量记录覆盖 {s['token_complete_runs']}/{s['runs']} 次运行。失败请求未知用量未按0估计。",
            f"- 模型逻辑请求 {s['model_requests']} 次，其中失败或未返回 {s['failed_model_requests']} 次。Provider内部网络重试未提供逐次计费信息。",
            '- 原始Harness的Token完整性字段只检查已成功返回的响应；本审计额外检查全部请求，超时运行的用量标为不完整。原始记录保留不改。',
            f"- 被拦截的安全事件 {s['security_blocked_events']} 个；工作区内最终未授权文件变更 {s['unauthorized_final_change_runs']} 次运行。阻止的尝试和已执行越界分别统计。本轮没有专门的对抗攻击任务，不能据此推断通用安全能力。",'',
            '## 失败归因','']
    for cause,count in s['failure_categories'].items(): lines.append(f'- `{cause}`：{count}次。')
    lines+=['','细分原因：']
    for cause,count in s['failure_subcategories'].items(): lines.append(f'- `{cause}`：{count}次。')
    lines+=['','工具事件计数（包含修复前预期失败的测试命令，不能直接当作运行失败次数）：']
    for cause,count in s['tool_issue_counts'].items(): lines.append(f'- `{cause}`：{count}个事件。')
    lines+=['','## 失败案例与证据','']
    for row in rows:
        if row['workflow_passed']: continue
        lines += [f"### {row['run_key']}",'',
                  f"- 功能验收：{row['functional_passed']}；失败归因：`{row['failure_category']}`；工具调用：{row['tool_steps']}。",
                  f"- 原始记录：`{row['raw_record']}`；轨迹：`{row['trace']}`；差异：`{row['diff']}`。"]
        lines += ['- '+detail.splitlines()[0] for detail in row['failure_evidence'][:4]]
        lines.append('')
    lines+=['## 边界','']+['- '+text for text in report['boundaries']]
    lines+=['','## 复现','',
            '运行 `python scripts/run_repair_suite.py --check` 核对冻结资产。',
            '使用新的输出目录执行 `python scripts/run_repair_suite.py --output artifacts/repair-suite-v1-reproduction`，并在启动前设置 `GIT_CEILING_DIRECTORIES` 为本项目 artifacts 的绝对路径。',
            '用 `python scripts/audit_repair_suite.py --output <批次目录> --audit-output <新审计目录>` 重新验证保存结果。',
            '原始模型轨迹和工作区保存在本地 artifacts，默认不进Git；审计JSON包含所有证据文件的SHA-256。','']
    path.write_text('\n'.join(lines),encoding='utf-8',newline='\n')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='artifacts/repair-suite-v1-2026-10-24')
    parser.add_argument('--audit-output',default='artifacts/repair-suite-v1-audit-2026-10-24')
    args=parser.parse_args()
    output=(ROOT/args.output).resolve(); destination=(ROOT/args.audit_output).resolve()
    if ROOT not in output.parents or ROOT not in destination.parents:
        parser.error('evidence and audit paths must be within project')
    audit(output,destination)

if __name__=='__main__':
    main()
