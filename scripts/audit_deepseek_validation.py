"""Independent verification of all three retained DeepSeek cohorts and latency probes."""
from collections import Counter
import json
from pathlib import Path
import re
import statistics
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import SUITE,VERIFY_COMMAND,aggregate,digest,inventory,load_json,utcnow,verify_pristine,write_json

AUDIT=ROOT/'artifacts/deepseek-independent-audit-2026-10-25'
PUBLIC=ROOT/'benchmarks/results/deepseek-official-2026-10-25'
BATCHES=[('before_adapter','deepseek-validation-2026-10-25'),
    ('dsml_only','deepseek-validation-dsml-2026-10-25'),('thinking_chat','deepseek-validation-thinking-2026-10-25')]


def events(path):
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def verify_source(batch,freeze):
    snapshot=batch/'source_snapshot.zip'
    if not snapshot.exists():
        for name,expected in freeze['source_hashes'].items():assert digest(ROOT/name)==expected,name
        with zipfile.ZipFile(snapshot,'x',zipfile.ZIP_DEFLATED) as z:
            for name in freeze['source_hashes']:z.write(ROOT/name,name)
            z.writestr('SOURCE_MANIFEST.json',json.dumps(freeze['source_hashes'],indent=2))
    import hashlib
    with zipfile.ZipFile(snapshot) as z:
        assert json.loads(z.read('SOURCE_MANIFEST.json'))==freeze['source_hashes']
        for name,expected in freeze['source_hashes'].items():assert hashlib.sha256(z.read(name)).hexdigest()==expected,name
    assert digest(batch/'plan.json')==freeze['plan_sha256']
    assert digest(batch/'protocol.json')==freeze['protocol_sha256']


def audit_batch(label,name):
    batch=ROOT/'artifacts'/name;freeze=load_json(batch/'freeze.json');verify_source(batch,freeze)
    results=load_json(batch/'results.json');plan=load_json(batch/'plan.json');tasks={t['id']:t for t in load_json(SUITE/'tasks.json')['tasks']}
    assert results['completed'] and len(results['rows'])==len(plan)==12
    assert results['configuration']==freeze['configuration']==load_json(batch/'protocol.json')
    assert len({(r['task_id'],r['repeat']) for r in results['rows']})==12
    assert set(Counter(r['task_id'] for r in results['rows']).values())=={2}
    rows=[];sessions=[];reply_latencies=[]
    for slot in plan:
        directory=batch/'runs'/slot['run_key'];raw=load_json(directory/'result.json')
        assert raw==next(r for r in results['rows'] if r['run_key']==slot['run_key'])
        task=tasks[slot['task_id']];workspace=directory/'workspace';after=inventory(workspace)
        assert raw['initial_sha256']==task['sha256'] and after==raw['final_sha256']
        changed=[n for n in set(after)|set(task['sha256']) if after.get(n)!=task['sha256'].get(n)]
        scope=set(changed)<=set(task['editable_files']) and all(after.get(n)==v for n,v in task['sha256'].items() if n not in task['editable_files'])
        implementation={n:(workspace/n).read_bytes() for n in task['editable_files'] if (workspace/n).is_file()}
        external=verify_pristine(task,implementation,AUDIT/'rechecks'/label/slot['run_key'])
        functional=bool(scope and len(implementation)==len(task['editable_files']) and external['accepted'])
        trace=ROOT/raw['trace'];assert digest(trace)==raw['trace_sha256'];ts=events(trace)
        writes=[i for i,e in enumerate(ts) if e['event']=='tool_executed' and e.get('workspace_changed')]
        passed=[i for i,e in enumerate(ts) if e['event']=='tool_executed' and e.get('name')=='run_shell'
            and e.get('args',{}).get('command','').strip()==VERIFY_COMMAND and e.get('exit_code')==0 and e.get('tool_status')=='ok']
        workflow=bool(functional and raw.get('status')=='completed' and not raw.get('error') and passed and max(passed)>max(writes,default=-1))
        assert functional==raw['functional_passed'] and workflow==raw['workflow_passed']
        assert external['exit_code']==raw['external_verifier']['exit_code'] and external['tests_run']==raw['external_verifier']['tests_run']
        io=events(directory/'model_io.jsonl');requests=[r for r in io if r['event']=='request'];responses=[r for r in io if r['event']=='response'];errors=[r for r in io if r['event']=='error']
        assert len(requests)==len(responses)+len(errors)
        for request in requests:
            assert Path(request['prompt'].split('- repo_root: ',1)[1].splitlines()[0].strip()).resolve()==workspace.resolve()
            assert 'reference_solutions.json' not in request['prompt']
        transport=events(directory/'transport.jsonl');failed=sum(not t['ok'] for t in transport)
        assert len(transport)==raw['transport_attempts'] and failed==raw['transport_failures']
        assert len([e for e in ts if e['event']=='model_transport'])==len(transport)
        for field in ('input_tokens','output_tokens'):
            values=[r.get('usage',{}).get(field) for r in responses]
            metric={'observed_total':sum(v for v in values if isinstance(v,int)),
                'observed_calls':sum(isinstance(v,int) for v in values),'successful_calls':len(responses),
                'complete':bool(values) and all(isinstance(v,int) for v in values) and not failed and not errors and not raw.get('error')}
            assert metric==raw[field]
        reply_latencies.extend(r['elapsed_ms'] for r in responses)
        normalizations=sum(r.get('usage',{}).get('dsml_normalization')=='normalized' for r in responses)
        dsml=sum('DSML' in r.get('raw_text','') or r.get('usage',{}).get('response_protocol')=='deepseek_dsml' for r in responses)
        malformed=sum(e['event']=='model_parsed' and e.get('kind')=='retry' for e in ts)
        reason='success' if workflow else 'model_error' if raw.get('error') else 'protocol_retry_limit' if raw.get('stop_reason')=='retry_limit_reached' else 'verification_incomplete' if raw.get('stop_reason')=='verification_incomplete' else raw.get('stop_reason') or 'implementation_or_workflow'
        rows.append({**raw,'independent_external_verifier':external,'failure_attribution':reason,
            'dsml_responses':dsml,'dsml_normalizations':normalizations,'malformed_responses':malformed})
        sessions.append(raw['session_id'])
        print(json.dumps({'cohort':label,'audited':len(rows),'run':slot['run_key'],'functional':functional,'workflow':workflow}),flush=True)
    assert len(set(sessions))==12
    summary=aggregate(rows);summary.update(transport_attempts=sum(r['transport_attempts'] for r in rows),transport_failures=sum(r['transport_failures'] for r in rows),
        malformed_responses=sum(r['malformed_responses'] for r in rows),dsml_normalizations=sum(r['dsml_normalizations'] for r in rows),
        failure_attributions=dict(Counter(r['failure_attribution'] for r in rows if not r['workflow_passed'])),
        successful_reply_latency_ms={'median':statistics.median(reply_latencies),'maximum':max(reply_latencies)})
    return {'label':label,'batch':batch.relative_to(ROOT).as_posix(),'configuration':freeze['configuration'],
        'freeze_sha256':digest(batch/'freeze.json'),'source_snapshot_sha256':digest(batch/'source_snapshot.zip'),'summary':summary,'rows':rows}


def build():
    AUDIT.mkdir(parents=True,exist_ok=False);PUBLIC.mkdir(parents=True,exist_ok=False)
    cohorts=[audit_batch(label,name) for label,name in BATCHES]
    from scripts.probe_deepseek_latency import check
    probe=ROOT/'artifacts/deepseek-latency-2026-10-25';check(probe);latency=load_json(probe/'results.json')
    assert latency['completed'] and len(latency['rows'])==7
    all_sessions=[r['session_id'] for c in cohorts for r in c['rows']];assert len(set(all_sessions))==36
    report={'at':utcnow(),'scope':'Three sequential known-task configurations, 6 tasks x 2 runs each. Adaptive development, not held-out validation.',
        'cohorts':cohorts,'latency_probe':latency,'latency_protocol':load_json(probe/'freeze.json'),
        'original_60_and_glm_12_results_unchanged':True}
    write_json(AUDIT/'audit.json',report,exclusive=True);write_json(PUBLIC/'audit.json',report,exclusive=True)
    lines=['# DeepSeek官方V4.1 Flash测试','',
        '使用官方 `https://api.deepseek.com`，请求模型ID `deepseek-flash`。官方文档将其映射到V4.1-Flash-1017；接口返回的模型字段为别名，不能据此验证部署权重版本。Key只保存本地，证据不含Key。','',
        '## 同一提示词的传输延迟','',
        '7次重放同一个已公开8356字符提示词，不执行返回工具。默认思考档位high；串行流式3次、三并发流式3次、非流式1次。',
        '| 请求 | 完成 | 首可见秒 | 总秒 | 思考Token |','|---|---|---:|---:|---:|']
    for r in latency['rows']:
        thought=(r.get('usage') or {}).get('completion_tokens_details',{}).get('reasoning_tokens','未知')
        visible=f"{r['first_visible']/1000:.3f}" if 'first_visible' in r else '—'
        lines.append(f"| {r['id']} | {r['ok']} | {visible} | {r['elapsed_ms']/1000:.3f} | {thought} |")
    lines+=['','此前GLM/硅基流动对同一提示词，6次流式中1次约78秒完成、5次240秒截止，另1次非流式180秒读取超时。本轮DeepSeek的较短思考输出是实际观测；这同时更换服务、模型、思考默认值和Token预算，不能单独归因于服务商。DeepSeek的8192上限包含思考，而原硅基流动3072不含思考；temperature=0.2虽然仍发送，但DeepSeek思考模式忽略它。','',
        '## 真实修复：每轮6题、每题2次','',
        '题目、测试、审批、隔离工作区与会话不变，各轮在开始前冻结；失败未筛除、替换或合并。所有36次结果均由独立外部验证器重新验收。',
        '| 配置 | 功能/12 | 完整流程/12 | 请求失败次数 | 无效回复次数 | 平均任务秒 |',
        '|---|---:|---:|---:|---:|---:|']
    for c in cohorts:
        s=c['summary'];lines.append(f"| {c['label']} | {s['functional_passes']} | {s['workflow_passes']} | {s['transport_failures']} | {s['malformed_responses']} | {s['elapsed_ms']['mean']/1000:.3f} |")
    lines+=['','- `before_adapter`：复用原Anthropic兼容适配，关闭思考，3072输出预算；模型大量输出DSML，原文本协议无法识别。',
        '- `dsml_only`：同一模式下规范化一个完整DSML调用，拒绝多调用、重复参数、截断及非JSON表达式；保留原始可见回复。仍观察到混合标记、实现修复不完整与验证失败。',
        '- `thinking_chat`：官方Chat Completions，默认启用思考/high，8192包含思考的输出预算，并沿用DSML兼容。API格式、模式、预算同时变化，是一次组合配置验证。',
        '- 每次写入仍经过允许文件列表和原有审批；结束前必须通过指定测试。更快的HTTP响应不等于任务修复正确。','',
        '## 分题结果','',
        '| 题目 | 首轮流程/2 | DSML轮流程/2 | 思考轮流程/2 |','|---|---:|---:|---:|']
    for task in sorted(cohorts[0]['summary']['per_task']):
        values=[str(c['summary']['per_task'][task]['workflow_passes'])+'/2' for c in cohorts]
        lines.append('| '+task+' | '+' | '.join(values)+' |')
    lines+=['','## 失败保留与证据','']
    for c in cohorts:
        lines.append(f"- `{c['label']}`：冻结 `{c['freeze_sha256']}`；完整轨迹、差异、首次外部验证位于 `{c['batch']}`；独立重测在 `artifacts/deepseek-independent-audit-2026-10-25/rechecks/{c['label']}`。")
        for r in c['rows']:
            if not r['workflow_passed']:
                lines.append(f"  - `{r['run_key']}`：{r['failure_attribution']}，修改文件{r['changed_files']}，外部退出码{r['independent_external_verifier']['exit_code']}。")
    lines+=['','## 限制与复现','',
        '三轮均使用已经看过的6道问题题目，配置是根据失败逐步调整的，不是独立留出评测；36次运行不等于36道独立题，也不更新原20题60次总体成功率。所有Token均按实际响应和失败传输记录；失败请求未知用量不计为0。',
        '每轮保留source_snapshot.zip，内含当时准确源码及清单；当前生产代码升级后旧freeze不应与当前源码混同。原v2严格检查应在它的原始源码快照内执行。真实重跑须使用与后端匹配的本地Key配置，归档从不含凭据。',
        '当前配置可用 `python scripts/run_deepseek_validation.py --freeze --output artifacts/new-deepseek-test`，再运行同脚本 `--run --output artifacts/new-deepseek-test`。输出目录不可覆盖；运行会产生真实API费用。','',
        '官方依据：[模型名与地址](https://api-docs.deepseek.com/)、[思考模式与temperature语义](https://api-docs.deepseek.com/guides/thinking_mode/)、[Anthropic兼容能力](https://api-docs.deepseek.com/guides/anthropic_api/)。','']
    (PUBLIC/'report.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    print(json.dumps({'cohort_scores':[c['summary']['workflow_passes'] for c in cohorts],'latency_completed':sum(r['ok'] for r in latency['rows'])}),flush=True)


def export():
    paths={}
    folders=[AUDIT,ROOT/'artifacts/deepseek-latency-2026-10-25']+[ROOT/'artifacts'/name for _,name in BATCHES]
    for folder in folders:
        for p in folder.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:paths[p.relative_to(ROOT).as_posix()]=p
    for p in PUBLIC.iterdir():
        if p.is_file() and p.suffix not in {'.zip','.sha256'}:paths[p.relative_to(ROOT).as_posix()]=p
    for name in ('scripts/run_deepseek_validation.py','scripts/probe_deepseek_latency.py','scripts/audit_deepseek_validation.py',
        'tests/test_deepseek_protocol.py','tests/test_deepseek_validation.py'):
        paths[name]=ROOT/name
    for name in ('deepseek-final-full-tests-2026-10-25.xml','deepseek-final-targeted-tests-v2-2026-10-25.xml','deepseek-official-smoke-2026-10-25.json'):
        p=ROOT/'artifacts'/name
        if p.exists():paths[p.relative_to(ROOT).as_posix()]=p
    secrets=[]
    for env in (ROOT/'.env',ROOT/'.env.deepseek.local',ROOT/'.env.before-deepseek-2026-10-25.local'):
        if not env.exists():continue
        for line in env.read_text(encoding='utf-8').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):continue
            k,v=line.split('=',1);v=v.strip().strip('\"\'')
            if len(v)>=8 and any(word in k.upper() for word in ('KEY','SECRET','TOKEN','PASSWORD')):secrets.append(v.encode())
    hashes={}
    def scan(name,data):
        assert not any(secret in data for secret in secrets),'Credential in '+name
        assert not re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}',data),name
    for name,path in paths.items():
        assert not path.name.startswith('.env'),name
        data=path.read_bytes();scan(name,data)
        if path.suffix=='.zip':
            with zipfile.ZipFile(path) as nested:
                for entry in nested.namelist():scan(name+'!'+entry,nested.read(entry))
        hashes[name]=digest(path)
    with zipfile.ZipFile(PUBLIC/'evidence.zip','x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,path in sorted(paths.items()):z.write(path,name)
        z.writestr('EVIDENCE_MANIFEST.json',json.dumps({'sha256':hashes,'credential_scan':'passed'},indent=2))
    sha=digest(PUBLIC/'evidence.zip')
    (PUBLIC/'evidence.sha256').write_text(sha+'  evidence.zip\n',encoding='ascii',newline='\n')
    print(json.dumps({'files':len(paths),'bytes':(PUBLIC/'evidence.zip').stat().st_size,'sha256':sha,'credential_scan':'passed'}),flush=True)


if __name__=='__main__':
    if '--export' in sys.argv:export()
    else:build()
