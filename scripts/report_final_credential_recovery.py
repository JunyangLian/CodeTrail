"""Audit additional credential recoveries and report them alongside, never instead of, first90."""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.run_final_agent_eval import DEFAULT,ASSETS,CONFIG,check,events
from scripts.run_final_credential_recovery import OUTPUT,SAFETY
from scripts.report_final_agent_eval import PUBLIC,AUDIT,audit_wire,csv_file,rate,pct
from pico.evaluation.repair_suite import load_json,write_json,digest,inventory,verify_pristine,utcnow
from pico.evaluation.seven_dimensions import run_metrics,aggregate_metrics,ratio


def attribution(row,directory):
    transport=events(directory/'transport.jsonl')
    statuses=Counter(str(e.get('status_code')) for e in transport if not e['ok'])
    error=row.get('error','')
    specific='api_billing_insufficient_balance' if 'HTTP 402' in error and '30001' in error else row['failure_category']
    return {'execution_id':directory.parent.parent.name+'/'+row['run_key'],'original_category':row['failure_category'],
            'specific_category':specific,'http_status_counts':dict(statuses),'evidence':(directory/'transport.jsonl').relative_to(ROOT).as_posix()}


def audit():
    check(DEFAULT);check(OUTPUT)
    protocol=load_json(OUTPUT/'recovery-protocol.json')
    assert digest(DEFAULT/'results.json')==protocol['original_results_sha256']
    original=load_json(DEFAULT/'results.json')['rows'];extra=load_json(OUTPUT/'results.json')['rows']
    assert len(extra)==54 and load_json(OUTPUT/'results.json')['completed']
    eligible={r['run_key'] for r in original if 'HTTP 402' in r.get('error','') and '30001' in r.get('error','')}
    assert {r['run_key'] for r in extra}==eligible=={s['run_key'] for s in protocol['slots']}
    for r in original:assert digest(DEFAULT/'runs'/r['run_key']/'result.json')==protocol['original_run_hashes'][r['run_key']]
    destination=AUDIT/'credential-recovery';destination.mkdir(exist_ok=False)
    tasks={t['id']:t for t in load_json(ASSETS/'tasks.json')['tasks']};gold=load_json(ASSETS/'evidence_gold.json')
    sessions=[r['session_id'] for r in original if r.get('session_id')];manifest={};wire_audits=[]
    for r in extra:
        d=OUTPUT/'runs'/r['run_key'];task=tasks[r['task_id']];ws=d/'workspace'
        assert r==load_json(d/'result.json') and inventory(ws)==r['final_sha256']
        assert r['initial_sha256']==task['sha256']
        impl={p:(ws/p).read_bytes() for p in task['editable_files'] if (ws/p).is_file()}
        verified=verify_pristine(task,impl,destination/'rechecks'/r['run_key'])
        scope=set(r['changed_files'])<=set(task['editable_files']) and all(r['final_sha256'].get(p)==h for p,h in task['sha256'].items() if p not in task['editable_files'])
        functional=bool(scope and len(impl)==len(task['editable_files']) and verified['accepted'])
        assert functional==r['functional_passed']
        tools=events(d/'tool_io.jsonl');wire=events(d/'wire_io.jsonl')
        assert run_metrics(task,r,tools,wire,gold[task['id']])==r['metrics']
        passing=[t for t in tools if t['event']=='response' and t['name']=='run_shell' and t['args'].get('command','').strip()==CONFIG['verification_command'] and t.get('metadata',{}).get('exit_code')==0]
        matches=bool(passing and passing[-1]['workspace_sha256_after']==r['final_sha256'])
        assert matches==r['final_matches_last_passing_test']
        assert bool(functional and r['status']=='completed' and r['agent_verified_after_last_write'] and matches and not r.get('error') and not r['controller_timeout'])==r['workflow_passed']
        if r.get('trace'):assert digest(ROOT/r['trace'])==r['trace_sha256']
        for a in events(d/'approvals.jsonl'):
            if a['allowed']:
                if a['name']=='run_shell':assert a['args']['command'].strip()==CONFIG['verification_command']
                else:assert (ws/a['args']['path']).resolve().relative_to(ws).as_posix() in task['editable_files']
        if r.get('session_id'):sessions.append(r['session_id'])
        wire_audits.append({'run_key':r['run_key'],**audit_wire(d)})
        manifest[r['run_key']]={p.relative_to(ROOT).as_posix():digest(p) for p in d.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        write_json(destination/'rechecks'/r['run_key']/'audit.json',{'functional':functional,'final_state_matches_retest':matches,'external':verified},exclusive=True)
        print('INDEPENDENT RECOVERY AUDIT '+r['run_key'],flush=True)
    assert len(sessions)==len(set(sessions))
    write_json(PUBLIC/'recovery-evidence-manifest.json',manifest,exclusive=True)
    all_rows=[('first90',DEFAULT,r) for r in original]+[('credential_recovery',OUTPUT,r) for r in extra]
    attributed=[attribution(r,d/'runs'/r['run_key']) for _,d,r in all_rows]
    write_json(PUBLIC/'failure-attribution.json',attributed,exclusive=True)
    groups={}
    for label,rows in [('first90',original),('credential_recovery',extra),('all144_executions',original+extra)]:
        groups[label]={name:aggregate_metrics(rs) for name,rs in {
          'all':rows,'existing':[r for r in rows if r['cohort']=='existing'],'holdout':[r for r in rows if r['cohort']=='holdout'],
          'real_upstream':[r for r in rows if r['origin_kind']!='author_created'],
          'self_designed':[r for r in rows if r['origin_kind']=='author_created'],
          'extra_tuned_six':[r for r in rows if r['development_tuned']]}.items()}
    safety=load_json(SAFETY/'results.json')
    report={'at':utcnow(),'first90_primary_unchanged':True,'additional_executions':len(extra),'total_repair_executions':len(all_rows),
      'recovery_protocol_sha256':digest(OUTPUT/'recovery-protocol.json'),'recovery_runner_sha256':protocol['script_sha256'],
      'postprocessor_sha256':digest(Path(__file__)),'groups':groups,'wire_audits':wire_audits,'independent_sessions':len(sessions),
      'additional_rows':extra,'safety':safety,'specific_failures':dict(Counter(x['specific_category'] for x in attributed)),
      'limit':'Additional executions selected only by a billing interruption. Do not replace first90 or describe all144 as an unchanged 90-run experiment. Credential identity and service time differ; no source, task, test, sampler or permission tuning.'}
    write_json(PUBLIC/'credential-recovery-audit.json',report,exclusive=True)
    write_json(destination/'audit.json',report,exclusive=True)
    write_json(PUBLIC/'additional-per-run.json',extra,exclusive=True)
    flat=[{'execution_id':label+'/'+r['run_key'],'batch':label,'run_key':r['run_key'],'task_id':r['task_id'],'cohort':r['cohort'],'repeat':r['repeat'],
       'functional_passed':r['functional_passed'],'workflow_passed':r['workflow_passed'],'failure_category':a['specific_category'],
       'protocol_errors':r['protocol_errors'],'tool_steps':r['metrics']['process']['tool_steps'],
       'duplicate_reads':r['metrics']['retrieval']['repeat_reads']['numerator'],'elapsed_ms':r['elapsed_ms'],
       'input_tokens_observed':r['metrics']['cost']['tokens']['input']['observed_total'],
       'output_tokens_observed':r['metrics']['cost']['tokens']['output']['observed_total'],
       'token_complete':all(r['metrics']['cost']['tokens'][n]['complete'] for n in ('input','output')),
       'error':r.get('error'),'workspace':r['workspace']} for (label,d,r),a in zip(all_rows,attributed)]
    csv_file(PUBLIC/'all-executions.csv',flat,list(flat[0]))
    distributions=[]
    for task in tasks.values():
        entry={'task_id':task['id'],'cohort':task['cohort'],'category':task['category'],'origin':task['origin']['kind']}
        for rep in (1,2,3):
            first=next(r for r in original if r['task_id']==task['id'] and r['repeat']==rep)
            recovery=next((r for r in extra if r['run_key']==first['run_key']),None)
            entry[f'r{rep}_first']=int(first['functional_passed']);entry[f'r{rep}_recovery']=int(recovery['functional_passed']) if recovery else None
        distributions.append(entry)
    write_json(PUBLIC/'per-task-with-recovery.json',distributions,exclusive=True);csv_file(PUBLIC/'per-task-with-recovery.csv',distributions,list(distributions[0]))
    lines=['# 凭据恢复追加实验：原90次全部保留','',
      '原批次90次中36次成功、54次HTTP402/code30001余额不足。用户随后提供替换凭据；在相同冻结源码、题目、验收、采样与权限下，按原顺序仅追加54个余额中断槽位，每个仍为全新工作区/会话。未对能力失败筛选重试。',
      '', '**修复执行总数为144次（90+54），仍只有30道不同题。** 原90次是主结果；追加结果是凭据恢复观察，不覆盖首批、不冒充无中断90次实验。6次独立安全模型实验另计。','',
      '| 批次/集合 | 不同题 | 运行 | 功能验收 | 完整流程 |','|---|---:|---:|---|---|']
    for batch,sets in groups.items():
        for name,g in sets.items():lines.append(f'| {batch}/{name} | {g["tasks"]} | {g["runs"]} | {rate(g["functional"])} | {rate(g["workflow"])} |')
    lines+=['','## 逐题首批/追加分布','','单元格为首批结果→追加结果；“-”表示该槽位没有追加。绝不以追加值改写首批。','',
        '| 题 | 集合 | r1 | r2 | r3 |','|---|---|---|---|---|']
    for e in distributions:lines.append('| '+e['task_id']+' | '+e['cohort']+' | '+' | '.join(str(e[f'r{n}_first'])+'→'+('-' if e[f'r{n}_recovery'] is None else str(e[f'r{n}_recovery'])) for n in (1,2,3))+' |')
    lines+=['','## 七维与过程对照','','以下列全144次观察，包含原余额失败；原90次和追加54次的各组同口径值见JSON，避免按成功筛选分母。','',
      '| 指标 | 全144次执行 |','|---|---|']
    g=groups['all144_executions']['all']
    for name,metric in [('功能验收',g['functional']),('完整流程',g['workflow']),('Recall@5',g['retrieval']['recall_at_k']['5']),
        ('必要片段覆盖',g['retrieval']['gold_span_coverage']),('上下文丢失代理',g['retrieval']['context_loss']),
        ('重复读取',g['retrieval']['repeat_reads']),('明确测试成功声明一致性（窄规则）',g['faithfulness']['explicit_claim_consistency']),
        ('无效调用',g['process']['invalid_calls']),('最后修改后重测',g['process']['retest_after_edit'])]:lines.append(f'| {name} | {rate(metric)} |')
    for name,value in [('协议重试',g['process']['protocol_retries']),('协议错误类型',g['process']['protocol_error_types']),
       ('工具步数/均值',g['process']),('Token',g['cost']),('安全拒绝/最终越权',g['safety'])]:lines.append(f'| {name} | `{json.dumps(value,ensure_ascii=False)}` |')
    lines+=['','完整分母、公式与N/A边界见[冻结方法](methodology.md)。余额故障产生的未观测Token保持未知，费用没有可归因账单；不能按0元计算。初始红灯测试不属于异常。时长不包含独立外部审计。',
      '', '## 失败归因与安全','',f'全部执行归因：`{report["specific_failures"]}`。HTTP402是明确账户余额故障，无证据归因为模型能力、请求太长、排队或超时。原始failure_category不改动；具体归因单独写入failure-attribution.json。',
      '',f'实际模型攻击：{safety["model_attack_passes"]}/{safety["model_attack_count"]}；脚本守卫：{safety["guard_passes"]}/{safety["guard_evaluated"]}。前一次no-live安全证据仍保留；不能把脚本守卫当作真实模型抗注入能力。']
    for r in extra:
        if not r['workflow_passed']:lines.append(f'- 追加失败 `{r["run_key"]}`：{r["failure_category"]}；{r.get("error")}。')
    for r in safety['model_attacks']:lines.append(f'- 模型安全 `{r["id"]}`：passed={r["passed"]}, workflow_completed={r["workflow_completed"]}, blocked={r["blocked_attempts"]}, error={r.get("error")}。')
    lines+=['','已批准代码执行仍能绕过文件工具读本地假canary；安全守卫失败保留。没有OS沙箱，不宣称任意仓库安全执行。语义记忆有效性、广义回答忠实性、专家工具选择准确率、实际货币费用均未评估。',
      '', '## 审计与复现','', '- 原90次报告及逐题/逐次文件保持原义；追加逐次JSON、全部执行CSV和逐题首批→追加JSON/CSV分别发布。',
      '- 追加54次逐一独立重测、重新计算过程指标、核对最终哈希/改后重测、原生帧和工具ID、审批及144个会话唯一性。',
      '- recovery-protocol.json在追加首次调用前记录选择规则、全部原结果哈希、脚本哈希和原冻结协议。账号/时间变化不构成模型或Harness单因素实验。',
      '- 历史GLM 43/60功能、42/60流程；官方DeepSeek旧Thinking 4/12，原生修复11/12。服务商、版本、Token语义和运行配置不同，仅作描述性参考。',
      '- 复核本发布使用 scripts/report_final_credential_recovery.py；重建原90次依冻结运行器，新凭据恢复只针对公开保留的402选择规则，输出目录拒绝覆盖。', '']
    (PUBLIC/'credential-recovery-report.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    print(json.dumps({'additional_audited':len(extra),'overall':groups['all144_executions']['all']['functional'],'unique_sessions':len(sessions)}))


if __name__=='__main__':audit()
