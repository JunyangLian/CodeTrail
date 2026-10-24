"""Independent audit of the separate twelve-run recovery regression."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from pico.evaluation.repair_suite import (SUITE,VERIFY_COMMAND,aggregate,digest,inventory,load_json,utcnow,verify_pristine,write_json)
from run_repair_regression_v2 import VERSION,CONFIG,TASK_IDS,frozen

def read_events(path):
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

def audit(output,destination):
    frozen();results=load_json(output/'results.json');plan=load_json(VERSION/'plan.json')
    assert results['completed'] and results['configuration']==CONFIG
    assert results['freeze_sha256']==digest(VERSION/'freeze.json')
    tasks={t['id']:t for t in load_json(SUITE/'tasks.json')['tasks']}
    assert len(results['rows'])==len(plan)==12
    assert Counter(r['task_id'] for r in results['rows'])=={t:2 for t in TASK_IDS}
    assert len({(r['task_id'],r['repeat']) for r in results['rows']})==12
    destination.mkdir(parents=True,exist_ok=False);rows=[];hashes={};sessions=[]
    for slot in plan:
        directory=output/'runs'/slot['run_key'];raw=load_json(directory/'result.json')
        assert raw==next(r for r in results['rows'] if r['run_key']==slot['run_key'])
        task=tasks[slot['task_id']];workspace=directory/'workspace';after=inventory(workspace)
        assert raw['initial_sha256']==task['sha256'] and after==raw['final_sha256']
        changes=[name for name in sorted(set(after)|set(task['sha256'])) if after.get(name)!=task['sha256'].get(name)]
        scope=set(changes)<=set(task['editable_files']) and all(after.get(n)==v for n,v in task['sha256'].items() if n not in task['editable_files'])
        implementation={n:(workspace/n).read_bytes() for n in task['editable_files'] if (workspace/n).is_file()}
        check=verify_pristine(task,implementation,destination/'rechecks'/slot['run_key'])
        functional=bool(scope and len(implementation)==len(task['editable_files']) and check['accepted'])
        trace=ROOT/raw['trace'];assert digest(trace)==raw['trace_sha256'];events=read_events(trace)
        writes=[i for i,e in enumerate(events) if e['event']=='tool_executed' and e.get('workspace_changed')]
        passed=[i for i,e in enumerate(events) if e['event']=='tool_executed' and e.get('name')=='run_shell'
                and e.get('args',{}).get('command','').strip()==VERIFY_COMMAND and e.get('exit_code')==0 and e.get('tool_status')=='ok']
        workflow=bool(functional and raw.get('status')=='completed' and not raw.get('error') and passed and max(passed)>max(writes,default=-1))
        assert functional==raw['functional_passed'] and workflow==raw['workflow_passed']
        assert check['exit_code']==raw['external_verifier']['exit_code'] and check['tests_run']==8
        calls=read_events(directory/'model_io.jsonl');requests=[c for c in calls if c['event']=='request'];responses=[c for c in calls if c['event']=='response'];errors=[c for c in calls if c['event']=='error']
        assert len(requests)==len(responses)+len(errors)
        for request in requests:
            assert Path(request['prompt'].split('- repo_root: ',1)[1].splitlines()[0].strip()).resolve()==workspace.resolve()
            assert 'reference_solutions.json' not in request['prompt']
        transport=read_events(directory/'transport.jsonl');failed=sum(not e['ok'] for e in transport)
        assert len(transport)==raw['transport_attempts'] and failed==raw['transport_failures']
        assert len([e for e in events if e['event']=='model_transport'])==len(transport)
        rejected=[e for e in events if e['event']=='completion_rejected']
        assert rejected==raw['completion_rejections']
        metrics={}
        for field in ('input_tokens','output_tokens'):
            values=[r.get('usage',{}).get(field) for r in responses]
            metrics[field]={'observed_total':sum(v for v in values if isinstance(v,int)),
                'observed_calls':sum(isinstance(v,int) for v in values),'successful_calls':len(responses),
                'complete':bool(values) and all(isinstance(v,int) for v in values) and not failed and not errors}
            assert metrics[field]==raw[field]
        row={**raw,**metrics,'independent_recheck':check,'completion_rejection_count':len(rejected),
             'raw_record':(directory/'result.json').relative_to(ROOT).as_posix(),'raw_record_sha256':digest(directory/'result.json')}
        rows.append(row);sessions.append(raw['session_id'])
        for p in directory.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:hashes[p.relative_to(ROOT).as_posix()]=digest(p)
        print(json.dumps({'audited':len(rows),'run_key':slot['run_key'],'functional':functional,'workflow':workflow,'transport_failures':failed,'completion_rejections':len(rejected)}),flush=True)
    assert len(set(sessions))==12
    summary=aggregate(rows)
    summary.update(transport_attempts=sum(r['transport_attempts'] for r in rows),transport_failures=sum(r['transport_failures'] for r in rows),
                   completion_rejections=sum(r['completion_rejection_count'] for r in rows),
                   runs_recovered_after_rejected_final=sum(r['workflow_passed'] and bool(r['completion_rejections']) for r in rows),
                   runs_completed_after_transport_failure=sum(r['workflow_passed'] and bool(r['transport_failures']) for r in rows),
                   final_timeout_runs=sum('TimeoutError' in r.get('error','') for r in rows),
                   runs_with_transport_failures=sum(bool(r['transport_failures']) for r in rows))
    baseline=load_json(ROOT/'benchmarks/results/repair-suite-v1-2026-10-24/audit.json')
    selected=[r for r in baseline['rows'] if r['task_id'] in TASK_IDS]
    assert len(selected)==18
    frozen()
    report={'audited_at':utcnow(),'auditor_sha256':digest(Path(__file__).resolve()),'configuration':CONFIG,
            'freeze_sha256':digest(VERSION/'freeze.json'),'baseline_full_score_unchanged':baseline['summary'],
            'baseline_selected_same_six_tasks':{'runs':18,'functional':sum(r['functional_passed'] for r in selected),'workflow':sum(r['workflow_passed'] for r in selected)},
            'summary':summary,'rows':rows,'evidence_sha256':hashes,
            'boundary':'Six previously observed problem tasks, two new runs each, combined runtime+timeout change. Not independent holdout, not a rerun of all20 tasks; do not replace the original60 or infer a causal effect from rates.'}
    write_json(destination/'audit.json',report,exclusive=True)
    s=summary;lines=['# CodeTrail 运行问题修复：单独回归报告','',
        f"本轮6道已知问题题目，每题2次，共12次。功能 **{s['functional_passes']}/12**，完整流程 **{s['workflow_passes']}/12**。全部新运行保留并独立重测。",'',
        '原始20题60次基线保持功能43/60、流程42/60，未覆盖或合并。本次选择已知问题题目，不是新的总体成功率或独立留出评测。',
        f"相同6题在旧基线中的18次记录：功能{report['baseline_selected_same_six_tasks']['functional']}/18，流程{report['baseline_selected_same_six_tasks']['workflow']}/18；样本次数、运行时与超时均有变化，不能作单因素因果推断。",'',
        '| 任务 | 功能通过/2 | 流程通过/2 |','|---|---:|---:|']
    for task,value in s['per_task'].items():lines.append(f"| {task} | {value['functional_passes']}/2 | {value['workflow_passes']}/2 |")
    lines+=['','## 修复与配置','',
        '- 普通进度文本不能作为最终回答。显式验证模式在最近一次修改后必须有指定命令的真实退出码0，失败/未测/过期时继续修复，预算耗尽或验证被拒绝时记录未完成。',
        '- 校验命令仍通过正常工具调用及审批执行；没有自动批准命令或自动改测试。只读解释任务不强制执行shell。',
        '- OpenAI兼容后端捕获读取超时、断连和不完整响应；最多3次传输尝试，重试前检查2倍配置超时的总预算，单次socket超时受剩余额度限制。401不重试，429使用有上限的等待。',
        '- 每次传输的错误类别、次数、耗时、是否重试及可用request_id进入轨迹。失败传输用量保持未知。',
        '- 请求超时由旧120秒改为180秒；模型、temperature0.2、20步预算和3072输出上限保持一致，单次任务上限1200秒。','',
        f"冻结指纹：`{report['freeze_sha256']}`。原始任务和验收测试哈希保持不变。",'',
        '## 观察到的机制行为','',
        f"- 拦截不满足验证的最终回答{s['completion_rejections']}次，其中{s['runs_recovered_after_rejected_final']}次运行随后完成修复和验证。",
        f"- 传输尝试{s['transport_attempts']}次，失败尝试{s['transport_failures']}次；涉及{s['runs_with_transport_failures']}次运行。未将失败传输的未知Token计为0。",
        f"- {s['runs_completed_after_transport_failure']}次运行在传输失败后重试并最终完成；{s['final_timeout_runs']}次最终因读取超时失败。有限重试不能保证外部服务可用。",
        '- 本轮没有触发completion_rejected事件；门槛拦截行为由软件回归测试验证。' if not s['completion_rejections'] else '- 完成门槛事件见逐次记录；组合干预不能证明门槛单独提升成功率。',
        f"- 可观测输入/输出Token：{s['input_tokens_observed']}/{s['output_tokens_observed']}；全请求用量完整覆盖{s['token_complete_runs']}/12次运行。",
        f"- 平均工具调用{s['tool_steps']['mean']:.2f}次，平均执行及外部验证{s['elapsed_ms']['mean']/1000:.3f}秒。",'',
        '## 逐次证据','']
    for r in rows:
        lines.append(f"- `{r['run_key']}`：功能{r['functional_passed']}，流程{r['workflow_passed']}，拒绝结束{r['completion_rejection_count']}次；原始记录 `{r['raw_record']}`；轨迹 `{r['trace']}`。")
    lines+=['','## 限制与复现','',report['boundary'],'',
        '超时恢复是有限重试机制，不能保证服务可用；若外部服务持续失败，任务仍会明确失败。重试可能增加等待和计费，失败请求用量未知。',
        '全量软件测试结果另见同目录JUnit。复现新批次使用 `python scripts/run_repair_regression_v2.py --output artifacts/recovery-v2-new`，输出目录不可覆盖。','']
    (destination/'report.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    print(json.dumps(summary),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',default='artifacts/repair-regression-v2-2026-10-24');p.add_argument('--audit-output',default='artifacts/repair-regression-v2-audit-2026-10-24')
    args=p.parse_args();output=(ROOT/args.output).resolve();destination=(ROOT/args.audit_output).resolve()
    if ROOT not in output.parents or ROOT not in destination.parents:p.error('paths must be inside project')
    audit(output,destination)

if __name__=='__main__':main()
