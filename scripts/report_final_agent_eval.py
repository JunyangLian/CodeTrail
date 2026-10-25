"""Independently audit every retained run, export seven-dimensional tables and evidence."""
from collections import Counter
import csv
import hashlib
from io import BytesIO
import json
from pathlib import Path
import platform
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import load_json,write_json,digest,inventory,verify_pristine,utcnow
from pico.evaluation.seven_dimensions import run_metrics,aggregate_metrics
from scripts.run_final_agent_eval import DEFAULT as BATCH,ASSETS,CONFIG,MODEL,check,events

PUBLIC=ROOT/'benchmarks/results/final-agent-90-2026-10-25'
AUDIT=ROOT/'artifacts/final-agent-independent-audit-2026-10-25'
FULL=ROOT/'artifacts/final-all-tests-2026-10-25.xml'
SAFETY=ROOT/'artifacts/final-agent-safety-2026-10-25'


def pct(value):
    return '未评估' if value is None else f'{value*100:.2f}%'


def rate(metric):
    return f"{metric['numerator']}/{metric['denominator']}（{pct(metric['value'])}）" if metric['denominator'] else '未评估（分母0）'


def csv_file(path,rows,columns):
    with path.open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader();writer.writerows(rows)


def audit_wire(directory):
    wire=events(directory/'wire_io.jsonl');previous=[];requests=0;responses=0;fingerprints=[]
    for event in wire:
        assert hashlib.sha256(event['body'].encode()).hexdigest()==event['sha256']
        body=json.loads(event['body'])
        if event['event']=='request':
            requests+=1
            assert body['model']==MODEL and body['max_tokens']==4096 and body['temperature']==.2 and body['top_p']==.95
            assert body['enable_thinking'] is True and body['thinking_budget']==4096
            assert {t['function']['name'] for t in body['tools']}==set(CONFIG['tools'])
            pending=set()
            for message in body['messages']:
                if message['role']=='assistant':
                    assert not pending
                    assert any(all(message.get(k)==candidate.get(k) and (k in message)==(k in candidate) for k in ('content','reasoning_content','tool_calls')) for candidate in previous),'Fabricated or altered assistant/reasoning frame'
                    pending={c['id'] for c in message.get('tool_calls') or []}
                elif message['role']=='tool':
                    assert message['tool_call_id'] in pending;pending.remove(message['tool_call_id'])
                else:assert not pending
            assert not pending
            assert 'holdout_references.json' not in event['body'] and 'reference_solutions.json' not in event['body']
        elif event['event']=='response':
            responses+=1;assert body.get('model')==MODEL
            fingerprints.append(body.get('system_fingerprint'))
            previous.extend(c['message'] for c in body.get('choices',[]))
    return {'requests':requests,'successful_responses':responses,'fingerprints':sorted({str(x) for x in fingerprints if x}),
            'frame_replay_checked':True,'missing_response_requests':requests-responses}


def audit_all():
    check(BATCH);data=load_json(BATCH/'results.json');plan=load_json(BATCH/'plan.json');tasks={t['id']:t for t in load_json(ASSETS/'tasks.json')['tasks']}
    assert len(plan)==90 and len({(s['task_id'],s['repeat']) for s in plan})==90
    AUDIT.mkdir(parents=True,exist_ok=False);PUBLIC.mkdir(parents=True,exist_ok=False)
    rows=[];sessions=[];manifests={};wire_audits=[]
    for original in data['rows']:
        slot=next(s for s in plan if s['run_key']==original['run_key']);task=tasks[slot['task_id']]
        directory=BATCH/'runs'/slot['run_key'];row=load_json(directory/'result.json');assert row==original
        workspace=directory/'workspace';assert inventory(workspace)==row['final_sha256']
        assert row['initial_sha256']==task['sha256']
        scope=set(row['changed_files'])<=set(task['editable_files']) and all(row['final_sha256'].get(p)==h for p,h in task['sha256'].items() if p not in task['editable_files'])
        implementation={p:(workspace/p).read_bytes() for p in task['editable_files'] if (workspace/p).is_file()}
        external=verify_pristine(task,implementation,AUDIT/'rechecks'/slot['run_key'])
        functional=bool(scope and len(implementation)==len(task['editable_files']) and external['accepted'])
        assert functional==row['functional_passed']
        if row.get('trace'):assert digest(ROOT/row['trace'])==row['trace_sha256']
        tools=events(directory/'tool_io.jsonl');wire=events(directory/'wire_io.jsonl')
        recomputed=run_metrics(task,row,tools,wire,load_json(ASSETS/'evidence_gold.json')[task['id']]);assert recomputed==row['metrics']
        passing=[t for t in tools if t['event']=='response' and t['name']=='run_shell' and t['args'].get('command','').strip()==CONFIG['verification_command'] and t.get('metadata',{}).get('exit_code')==0]
        matches=bool(passing and passing[-1]['workspace_sha256_after']==row['final_sha256'])
        assert matches==row['final_matches_last_passing_test']
        expected_workflow=bool(functional and row['status']=='completed' and row['agent_verified_after_last_write'] and matches and not row.get('error') and not row['controller_timeout'])
        assert expected_workflow==row['workflow_passed']
        if row.get('session_id'):sessions.append(row['session_id'])
        for approval in events(directory/'approvals.jsonl'):
            if approval['allowed']:
                if approval['name']=='run_shell':assert approval['args']['command'].strip()==CONFIG['verification_command']
                else:
                    p=(workspace/approval['args']['path']).resolve()
                    assert p.relative_to(workspace).as_posix() in task['editable_files']
        wire_audits.append({'run_key':slot['run_key'],**audit_wire(directory)})
        manifests[slot['run_key']]={p.relative_to(ROOT).as_posix():digest(p) for p in directory.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        rows.append({**row,'independent_external_verifier':external})
        print(json.dumps({'audited':len(rows),'run':row['run_key'],'functional':functional,'workflow':expected_workflow}),flush=True)
    assert len(sessions)==len(set(sessions)),'Reused session'
    groups={
      'all':rows,'existing':[r for r in rows if r['cohort']=='existing'],'holdout':[r for r in rows if r['cohort']=='holdout'],
      'real_upstream':[r for r in rows if r['origin_kind']!='author_created'],
      'self_designed':[r for r in rows if r['origin_kind']=='author_created'],
      'extra_tuned_six':[r for r in rows if r['development_tuned']],
      'existing_other_fourteen':[r for r in rows if r['cohort']=='existing' and not r['development_tuned']]}
    summaries={name:aggregate_metrics(group) for name,group in groups.items()}
    root=ET.parse(FULL).getroot();suites=list(root.iter('testsuite'))
    tests={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert tests['failures']==tests['errors']==0
    security=load_json(SAFETY/'results.json') if (SAFETY/'results.json').exists() else {'status':'not_evaluated'}
    report={'at':utcnow(),'complete_90':data['completed'],'planned_runs':90,'observed_runs':len(rows),
      'summaries':summaries,'rows':rows,'full_tests':tests,'security':security,
      'wire_audits':wire_audits,'freeze_sha256':digest(BATCH/'freeze.json'),
      'plan_sha256':digest(BATCH/'plan.json'),'assets_sha256':digest(ASSETS/'tasks.json'),
      'gold_sha256':digest(ASSETS/'evidence_gold.json'),'source_snapshot_sha256':digest(BATCH/'source_snapshot.zip'),
      'system':{'python':sys.version,'platform':platform.platform(),'architecture':platform.machine()},
      'limits':['Only 30 distinct tasks; 90 runs are repetitions, not independent tasks.',
        'All old 20 were previously evaluated, six additionally tuned; no held-out claim for them.',
        'New ten were not used for project Agent tuning but public upstream bugs may occur in model pretraining.',
        'New source-package tasks and old minimal repro tasks differ in difficulty and representation.',
        'Provider model ID is mutable; immutable weight revision unavailable.',
        'Semantic memory effectiveness, broad natural-language faithfulness and expert tool-selection accuracy unassessed.',
        'No OS sandbox; approved code executes with current user privileges. See the explicit code-execution boundary security case.',
        'Actual attributable currency billing unavailable; missing Token usage is unknown, never imputed zero.']}
    write_json(AUDIT/'audit.json',report,exclusive=True);write_json(PUBLIC/'audit.json',report,exclusive=True)
    write_json(PUBLIC/'per-run.json',rows,exclusive=True);write_json(PUBLIC/'run-evidence-manifest.json',manifests,exclusive=True)
    flat=[]
    for r in rows:
        m=r['metrics'];flat.append({'run_key':r['run_key'],'task_id':r['task_id'],'cohort':r['cohort'],'repeat':r['repeat'],
         'origin':r['origin_kind'],'development_tuned':r['development_tuned'],'functional_passed':r['functional_passed'],
         'workflow_passed':r['workflow_passed'],'tool_steps':m['process']['tool_steps'],'protocol_errors':r['protocol_errors'],
         'duplicate_reads':m['retrieval']['repeat_reads']['numerator'],'successful_reads':m['retrieval']['repeat_reads']['denominator'],
         'recall_at_5':m['retrieval']['recall_at_k']['5']['value'],'span_coverage':m['retrieval']['gold_span_coverage']['value'],
         'context_loss':m['retrieval']['context_loss']['value'],'input_tokens_observed':m['cost']['tokens']['input']['observed_total'],
         'output_tokens_observed':m['cost']['tokens']['output']['observed_total'],
         'token_complete':all(m['cost']['tokens'][n]['complete'] for n in ('input','output')),'currency_cost':None,
         'elapsed_ms':r['elapsed_ms'],'blocked_attempts':m['safety']['blocked_attempts'],
         'final_matches_last_passing_test':r['final_matches_last_passing_test'],'failure_category':r['failure_category'],
         'error':r.get('error'),'trace':r.get('trace')})
    if flat:csv_file(PUBLIC/'per-run.csv',flat,list(flat[0]))
    per_task=[]
    for task in tasks.values():
        group=[r for r in rows if r['task_id']==task['id']]
        item={'task_id':task['id'],'cohort':task['cohort'],'category':task['category'],'origin':task['origin']['kind'],
              'observed_runs':len(group),'functional_passes':sum(r['functional_passed'] for r in group),
              'workflow_passes':sum(r['workflow_passed'] for r in group),
              **{f'r{n}':next((int(r['functional_passed']) for r in group if r['repeat']==n),None) for n in (1,2,3)}}
        per_task.append(item)
    write_json(PUBLIC/'per-task.json',per_task,exclusive=True);csv_file(PUBLIC/'per-task.csv',per_task,list(per_task[0]))
    shutil.copyfile(FULL,PUBLIC/'full-tests.xml');shutil.copyfile(ASSETS/'methodology.md',PUBLIC/'methodology.md')
    write_report(report,per_task)
    return report


def write_report(report,per_task):
    summaries=report['summaries'];all=summaries['all'];security=report['security']
    lines=['# CodeTrail：最终代码修复与七维工程评测','',
       f'计划30道不同题、90次独立会话；实际保留{report["observed_runs"]}次结果。正式90次完成：{report["complete_90"]}。原始历史基线和所有失败保留，未替换运行。','',
       '## 三个核心问题','',
       f'**能否修好代码？** 本轮功能验收 {rate(all["functional"])}，完整流程 {rate(all["workflow"])}；以下必须分开看原题和新留出，不能把调优收益当作未见任务泛化。',
       '**过程是否可靠、安全、可验证？** 使用独立原始测试、完整模型请求/响应、实际工具结果、最终哈希与改后复测核对。安全测试独立报告；应用层权限不等于OS沙箱，未评估部分不填漂亮分数。',
       '**优化是否有客观证据？** 有冻结版本、全量测试、逐次外部复验和失败归因。与历史实验只作描述性比较：模型、服务商、Thinking、Token语义和Harness都有差异，没有本轮单因素消融或显著性结论。','',
       '## 分组结果','', '| 集合 | 不同题 | 实际运行 | 功能验收 | 完整流程 |','|---|---:|---:|---|---|']
    labels={'all':'全部','existing':'原有20题','holdout':'全新10题','real_upstream':'真实上游故障','self_designed':'自设计题','extra_tuned_six':'额外开发调优6题','existing_other_fourteen':'其余原有14题'}
    for key,s in summaries.items():lines.append(f'| {labels[key]} | {s["tasks"]} | {s["runs"]} | {rate(s["functional"])} | {rate(s["workflow"])} |')
    lines+=['','新10题来自more-itertools与python-dotenv的精确历史提交，保留原始包源码和许可证；旧真实故障主要为最小复现。全部为公开源码，不保证模型预训练未见；这里只保证新10题此前未用于本项目Agent开发调优。参考实现和补丁未进入工作区或公开证据包。','',
      '## 七维总表','', '| 维度 | 原有20题 | 新留出10题 |','|---|---|---|']
    old=summaries['existing'];new=summaries['holdout']
    def metric_row(label,path):
        def get(s):
            for p in path:s=s[p]
            return s
        lines.append(f'| {label} | {rate(get(old))} | {rate(get(new))} |')
    metric_row('1 功能/外部验收',('functional',));metric_row('1 完整流程',('workflow',))
    for k in ('3','5','10'):metric_row('2 必要文件 Recall@'+k,('retrieval','recall_at_k',k))
    for label,key in [('2 冻结必要片段送达覆盖','gold_span_coverage'),('2 已取得金标行上下文丢失','context_loss'),('2 重复读取','repeat_reads')]:metric_row(label,('retrieval',key))
    metric_row('3 明确测试成功声明一致性（规则）',('faithfulness','explicit_claim_consistency'))
    metric_row('4 无效调用',('process','invalid_calls'));metric_row('4 首次编辑前测试',('process','initial_test_before_edit'));metric_row('4 最后修改后重测',('process','retest_after_edit'))
    lines.append(f'| 5 三次均通过题 | {rate(old["all_three_success_tasks"])} | {rate(new["all_three_success_tasks"])} |')
    lines.append(f'| 5 协议重试 | {old["process"]["protocol_retries"]} | {new["process"]["protocol_retries"]} |')
    lines.append(f'| 6 可观测输入/输出Token | {old["cost"]["token_observed_totals"]} | {new["cost"]["token_observed_totals"]} |')
    lines.append(f'| 6 Token完整运行 | {old["cost"]["token_complete_runs"]}/{old["runs"]} | {new["cost"]["token_complete_runs"]}/{new["runs"]} |')
    lines.append(f'| 6 平均工具步 | {old["process"]["mean_tool_steps"]} | {new["process"]["mean_tool_steps"]} |')
    lines.append(f'| 6 时长ms（p50/p95/平均） | {old["cost"]["duration_ms"]} | {new["cost"]["duration_ms"]} |')
    lines.append(f'| 7 拒绝尝试/最终未授权变更 | {old["safety"]} | {new["safety"]} |')
    lines+=['','完整公式、分母、代理指标及局限见[冻结方法](methodology.md)。真实任务长期记忆召回有效性、广义自然语言忠实性/证据遗漏/自由文本归因，以及专家标注的工具选择准确率均未评估。记忆3个机械专项不等同真实任务效果。Token为可观测下界；未知请求用量不填零。平台未提供可归因货币账单，因此真实费用、单位成功任务货币成本为未评估。','',
      '## 30题逐题三次结果','', '| 题 | 集合/类型 | r1 | r2 | r3 | 功能 | 流程 |','|---|---|---:|---:|---:|---:|---:|']
    for t in per_task:lines.append(f'| {t["task_id"]} | {t["cohort"]}/{t["category"]} | {t["r1"]} | {t["r2"]} | {t["r3"]} | {t["functional_passes"]}/{t["observed_runs"]} | {t["workflow_passes"]}/{t["observed_runs"]} |')
    lines+=['',f'原题0/3到3/3分布：{old["task_success_distribution"]}；留出分布：{new["task_success_distribution"]}。三次重复不能替代三个独立任务。','',
      '## 失败与异常恢复','',f'失败分类：{all["failures"]}。所有逐次错误、完整轨迹、代码差异与外部测试结果均在per-run.json和证据包中。']
    failures=[r for r in report['rows'] if not r['workflow_passed']]
    if not failures:lines.append('本轮未观察到失败；不据此声称零风险，亦不编造失败案例。历史失败与独立注入测试保留。')
    for r in failures:lines.append(f'- `{r["run_key"]}`：{r["failure_category"]}；error={r.get("error")!r}；trace=`{r.get("trace")}`。')
    persistence=[e for r in report['rows'] for e in r['persistence_events']]
    transport=[e for r in report['rows'] for e in events(BATCH/'runs'/r['run_key']/'transport.jsonl')]
    lines += ['',f'正式运行持久化事件：{dict(Counter(e["event"] for e in persistence))}；传输失败：{sum(not e["ok"] for e in transport)}，按失败阶段：{dict(Counter(e.get("phase","unknown") for e in transport if not e["ok"]))}。连接/响应头阶段无法进一步证明是DNS、TLS或服务端排队。',
       f'协议错误类型：{all["process"]["protocol_error_types"]}；初始故障测试非零属于预期诊断，不计为API或状态恢复失败。','',
      '## 独立安全及最小专项','',f'安全汇总：脚本守卫 {security.get("guard_passes")}/{security.get("guard_evaluated")}；真实模型README攻击 {security.get("model_attack_passes")}/{security.get("model_attack_count")}。安全分母不进入90次修复率。']
    for r in security.get('guard_cases',[]):lines.append(f'- guard `{r["id"]}`：evaluated={r["evaluated"]}，passed={r.get("passed")}；{r.get("reason",r.get("criterion"))}。')
    for r in security.get('model_attacks',[]):lines.append(f'- 实际模型 `{r["id"]}`：passed={r["passed"]}，canary工具暴露={r["canary_returned_by_tool"]}，拒绝数={r["blocked_attempts"]}，error={r.get("error")!r}。')
    lines += ['',f'机械记忆与持久化专项：{security.get("special_cases")}。其中真实IO探测、受控故障注入单元测试与实际模型攻击必须分开解释。被批准的代码+测试进程以用户身份执行，能绕过应用层文件工具限制；这个安全边界案例即使失败也如实保留，未修改ACL或扩大权限。','',
      '## 典型案例与历史比较','']
    for cohort in ('existing','holdout'):
        example=next((r for r in report['rows'] if r['cohort']==cohort and r['workflow_passed']),None)
        if example:lines.append(f'- 按计划顺序首个{cohort}成功案例：`{example["run_key"]}`，工具{example["metrics"]["process"]["tool_steps"]}步，差异与测试见同名运行目录。')
    lines+=['','| 历史实验 | 功能 | 流程 | 边界 |','|---|---:|---:|---|',
       '| 原GLM/硅基流动20×3 | 43/60 | 42/60 | 旧Harness、3072输出、不同模型/超时 |',
       '| 官方DeepSeek Thinking 6×2 | 4/12 | 4/12 | 默认high，8192包含思考，旧文本协议 |',
       '| 官方DeepSeek原生修复6×2 | 11/12 | 11/12 | 20工具步；1次Windows状态替换失败；原结果保留 |',
       '','本轮使用硅基流动、独立思考/输出预算、状态有限重试及更严格敏感路径规则。旧配置不同，没有构成严格单因素消融；既不将新模型收益归因于Harness，也不把已知题收益冒充留出泛化。','',
       '## 审计与复现','',f'- 全量软件测试：{report["full_tests"]}；不是独立Agent任务数。',
       f'- 冻结SHA256：`{report["freeze_sha256"]}`；计划SHA256：`{report["plan_sha256"]}`。',
       '- 请求/响应体包括第三方API实际返回的reasoning，仅针对本轮授权公开题；Authorization不落盘。原生assistant帧及tool_call_id配对由独立审计校验。',
       '- 每次运行哈希见run-evidence-manifest.json；逐运行、逐题JSON/CSV、全量JUnit、完整轨迹、差异、外部复验均保留。新参考补丁和控制器fixed资产不发布，不安装到Agent工作区。',
       '- 依赖实际服务返回的模型别名而非不可变权重ID；真实部署可变，冻结代码不保证逐字生成一致。',
       '- 冻结代码下运行：设置环境变量SILICONFLOW_API_KEY，执行 `python -X utf8 -m pytest tests -q`；在新输出目录执行 `python scripts/run_final_agent_eval.py --output artifacts/your-new-run --freeze`，再以同一--output执行--run。现存输出拒绝覆盖。复现应从本仓库副本覆盖解压对应source_snapshot.zip，保留原始v1金标/归档；本轮留出一旦用于后续改进须降级为开发集。','',
       '模型依据：[实际模型列表接口](https://docs.siliconflow.cn/docs/api/models-get)、[版本公告](https://docs.siliconflow.cn/docs/release-notes/overview)、[Chat Completions参数](https://docs.siliconflow.cn/docs/api/chat-completions-post)。','']
    (PUBLIC/'report.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')


def export_evidence(report):
    import os
    from pico.config import _parse_env_line
    secrets={v.encode() for k,v in os.environ.items() if any(w in k.upper() for w in ('API_KEY','TOKEN','SECRET','PASSWORD')) and len(v)>=8}
    for project in (ROOT,ROOT.parent/'pico-main'):
        for p in project.glob('.env*'):
            if not p.is_file() or p.name=='.env.example':continue
            for line in p.read_text('utf-8').splitlines():
                item=_parse_env_line(line)
                if item and any(w in item[0].upper() for w in ('API_KEY','TOKEN','SECRET','PASSWORD')) and len(item[1])>=8:secrets.add(item[1].encode())
    files={}
    for directory in (BATCH,AUDIT,SAFETY,ROOT/'artifacts/final-eval-preflight-2026-10-25'):
        for p in directory.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:files[p.relative_to(ROOT).as_posix()]=p
    for p in (ROOT/'artifacts').glob('final-*.xml'):files[p.relative_to(ROOT).as_posix()]=p
    for name in ['README.md','docs/final-agent-interview.md','docs/final-agent-architecture.md']:
        p=ROOT/name
        if p.exists():files[name]=p
    scanned=0
    def scan(name,body):
        nonlocal scanned
        scanned+=1;assert not any(s in body for s in secrets),'Credential in '+name
        if name.endswith('.zip'):
            with zipfile.ZipFile(BytesIO(body)) as archive:
                for entry in archive.namelist():
                    assert 'final-private-gold' not in entry and not entry.endswith('holdout_references.json')
                    scan(name+'!'+entry,archive.read(entry))
    hashes={}
    for name,p in sorted(files.items()):
        assert not p.name.startswith('.env'),name
        scan(name,p.read_bytes());hashes[name]=digest(p)
    destination=PUBLIC/'evidence.zip'
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,p in sorted(files.items()):archive.write(p,name)
        archive.writestr('EVIDENCE_MANIFEST.json',json.dumps({'sha256':hashes,'credential_scan':'passed',
            'scanned_entries':scanned,'private_reference_solutions_excluded':True},indent=2))
    (PUBLIC/'evidence.sha256').write_text(digest(destination)+'  evidence.zip\n',encoding='ascii',newline='\n')
    print(json.dumps({'archive':str(destination),'bytes':destination.stat().st_size,'sha256':digest(destination),'credential_scan':'passed'}),flush=True)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--export-only',action='store_true');args=p.parse_args()
    export_evidence(load_json(PUBLIC/'audit.json')) if args.export_only else audit_all()
