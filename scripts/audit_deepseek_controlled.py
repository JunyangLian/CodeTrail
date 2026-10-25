"""Independently reverify the fixed-plan native retest and publish honest comparisons."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import digest,load_json,write_json,utcnow
from scripts.analyze_deepseek_traces import analyze,events,BATCHES
import scripts.audit_deepseek_validation as independent

NAME='deepseek-controlled-2026-10-25'
BATCH=ROOT/'artifacts'/NAME
DEST=ROOT/'benchmarks/results'/NAME
AUDIT=ROOT/'artifacts/deepseek-controlled-audit-2026-10-25'
FULL=ROOT/'artifacts/native-release-full-tests-2026-10-25.xml'


def verify_native_protocol(batch):
    rows=[]
    for slot in load_json(batch/'plan.json'):
        directory=batch/'runs'/slot['run_key'];io=events(directory/'model_io.jsonl')
        requests=[e for e in io if e['event']=='request'];responses=[e for e in io if e['event']=='response']
        observed=[];paired=0
        for request in requests:
            wire=request['native_request'];messages=wire['messages']
            assert wire['thinking']=={'type':'enabled'} and wire['reasoning_effort']=='high'
            assert wire['tool_choice']=='auto'
            assert all('reasoning_content' not in m for m in messages)
            assert {t['function']['name'] for t in wire['tools']}=={'list_files','read_file','search','patch_file','write_file','run_shell'}
            pending=set();assistant=[]
            for message in messages:
                if message['role']=='assistant':
                    assert not pending
                    assistant.append(message['reasoning_audit'])
                    for call in message.get('tool_calls') or []:
                        assert call['id'] not in pending;pending.add(call['id'])
                elif message['role']=='tool':
                    assert message['tool_call_id'] in pending
                    pending.remove(message['tool_call_id']);paired+=1
                else:assert not pending
            assert not pending
            previous=[r for r in responses if r['index']<request['index'] and r['usage'].get('reasoning_present')
                      and r['usage'].get('provider_finish_reason')!='length'
                      and r['usage'].get('protocol_error')!='Invalid or duplicate tool IDs; no action accepted.']
            # All retained assistant reasoning fingerprints match previously
            # observed responses, in order. No synthesized assistant tool frames.
            expected=[{'chars':r['usage']['reasoning_chars'],'sha256':r['usage']['reasoning_sha256']} for r in previous]
            assert assistant==expected
            observed=assistant
        rows.append({'run_key':slot['run_key'],'requests':len(requests),'replayed_reasoning_frames_last_request':len(observed),
                     'tool_result_pairings_across_requests':paired,'passed':True})
    return rows


def main():
    AUDIT.mkdir(exist_ok=False);DEST.mkdir(parents=True,exist_ok=False)
    baseline=ROOT/'artifacts'/BATCHES[-1]
    assert (BATCH/'plan.json').read_bytes()==(baseline/'plan.json').read_bytes()
    freeze=load_json(BATCH/'freeze.json');old=load_json(baseline/'freeze.json')
    gold='benchmarks/repair_suite_v1/tasks.json'
    assert freeze['source_hashes'][gold]==old['source_hashes'][gold]==digest(ROOT/gold)
    independent.AUDIT=AUDIT
    audited=independent.audit_batch('native_controlled',NAME)
    protocol=verify_native_protocol(BATCH)
    analysis=[analyze(ROOT/'artifacts'/name) for name in BATCHES+[NAME]]
    before=load_json(ROOT/'artifacts/deepseek-root-causes-before-2026-10-25.json')
    assert [c['results_sha256'] for c in analysis[:3]]==[c['results_sha256'] for c in before['cohorts']]
    all_ids=[r['session_id'] for name in BATCHES+[NAME] for r in load_json(ROOT/'artifacts'/name/'results.json')['rows']]
    assert len(all_ids)==len(set(all_ids))==48
    tree=ET.parse(FULL);suites=list(tree.getroot().iter('testsuite'))
    tests={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert tests['failures']==tests['errors']==0
    attribution=[]
    for row in audited['rows']:
        if row['workflow_passed']:category='success'
        elif 'PermissionError' in row.get('error','') and 'task_state.json' in row.get('error',''):category='runtime_state_persistence_error'
        elif row.get('error'):category='provider_or_runtime_error'
        elif row.get('stop_reason')=='retry_limit_reached':category='protocol_retry_limit'
        elif not row['functional_passed']:category='incomplete_fix_or_test_regression'
        else:category='workflow_verification_incomplete'
        attribution.append({'run_key':row['run_key'],'category':category,'evidence':row['trace'],
                            'external':row['independent_external_verifier']})
    report={'at':utcnow(),'scope':'Same 6 independent tasks x 2 runs. No new task or replacement; known-task development evaluation.',
        'freeze_sha256':digest(BATCH/'freeze.json'),'same_plan_sha256':digest(BATCH/'plan.json'),
        'task_manifest_sha256':digest(ROOT/gold),'source_snapshot_sha256':digest(BATCH/'source_snapshot.zip'),
        'independent_audit':audited,'native_protocol_audit':protocol,'trace_analysis':analysis,
        'failure_attribution':attribution,'full_tests':tests,'full_tests_sha256':digest(FULL),
        'limitations':['Only 6 known independent problems, two runs each; no generalization or significance claim.',
            'Bundled fixes change protocol, context delivery and error feedback together; no individual-fix causal claim.',
            'Reasoning bodies are replayed only in memory. Audit retains presence, length and hash; exact historical HTTP body replay is unavailable.',
            'Provider alias/weights and sampling may vary. Reproduction reruns the frozen source/plan; exact model outputs are not guaranteed.']}
    diagnosis=AUDIT/'io-diagnosis/diagnosis.json'
    if diagnosis.exists():report['persistence_diagnosis']=load_json(diagnosis)
    write_json(AUDIT/'audit.json',report,exclusive=True);write_json(DEST/'audit.json',report,exclusive=True)
    shutil.copyfile(FULL,DEST/'full-tests.xml')
    shutil.copyfile(ROOT/'artifacts/deepseek-root-causes-before-2026-10-25.json',DEST/'before-analysis.json')
    s0=analysis[2]['summary'];s1=analysis[3]['summary']
    patch_failures=sum(sum(f['name']=='patch_file' for f in r['tool_failure_evidence']) for r in analysis[3]['rows'])
    patch_failure_runs=[r for r in analysis[3]['rows'] if any(f['name']=='patch_file' for f in r['tool_failure_evidence'])]
    recovered_patch_runs=sum(r['workflow_passed'] for r in patch_failure_runs)
    lines=['# DeepSeek 修复链路：根因分析与受控复测','',
        '复测沿用 **6 道独立冻结题 × 每题 2 次 = 12 次运行**。这是已有 12 个运行槽位，不是 12 道独立题。未新增题目、修改金标、替换失败、扩大工具权限或删除旧记录。','',
        '## 36 次旧轨迹的根因','',
        '1. **协议冲突与补丁污染**：三轮旧实验都将历史拼成单条 user 消息，未发送原生 `tools`。首轮 201 次、DSML 轮 1 次、Thinking 轮 31 次解析重试。首轮 `11-humanize_size-r2` 第 15 步、DSML 轮 `01-humanize_size-r1` 第 17 步的补丁参数带 DSML 结束标记，且执行状态为 ok。兼容 DSML 只减少格式失败，没有解决上下文丢失。',
        '2. **Thinking 回传口径**：旧请求未携带 `tools`，官方文档说明这种模式不要求回传 `reasoning_content`。旧日志未保存思考正文，不能断言这是旧失败的直接原因。修复后采用原生工具，按要求回传每个历史 assistant 消息的 reasoning、content、真实 tool_calls，再以实际 ID 配对 tool 结果；正文仅在内存保存，证据记录哈希。',
        f'3. **重复读取与上下文丢失**：旧 Thinking 轮实际读取 {s0["executed_reads"]} 次，其中 {s0["duplicate_reads"]} 次为同参数、同输出的重复读取。{s0["reads_with_missing_lines"]} 次读取返回的部分源码行不在下一轮实际请求中；最近工具反馈被历史渲染压到 900 字符，代码主体/断言可能被截断。`01-humanize_size-r1` 首次读源码返回 109 行，下一轮缺失 71 行。修复后原生会话保留工具结果，长文件按实际返回行数分页并给出下一段范围，shell 反馈上限 12000 字符。',
        '4. **纠错反馈失效**：旧 Thinking 轮 `12-inventory_reservation-r2` 第 6 步 patch 缺少 path；旧错误示例仍引导 XML，并存在重复调用后直接建议 final 的反馈。修复后提供明确的“未写入、读取当前位置、复制精确 old_text、缩小唯一片段”提示；失败测试的输出进入原生 tool 消息，完成门禁仍要求最后一次编辑后通过原命令。原生协议拒绝多调用、非法 JSON、截断、缺失 reasoning 和混合 DSML，不执行其中任何写入。','',
        '官方依据：[Thinking 模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[工具调用](https://api-docs.deepseek.com/guides/tool_calls/)。上述为日志证据与实现分析；不能用观察性记录证明每个改动的独立因果效果。','',
        '## 相同设置的复测','',
        '`deepseek-flash`，官方 `/v1/chat/completions`，Thinking enabled/high（原轮为官方相同默认值），temperature=0.2 仍发送但被 Thinking 忽略，max_tokens=8192（包含思考），20 个工具步、最多 60 次尝试，socket timeout=180 秒，单次运行截止 900 秒，并发 3。计划顺序逐字复用；每次新工作区/会话，原审批、可修改文件和外部验收保持不变。原生协议和上下文交付同时变化，输入 Token 成本须结合结果看。','',
        '| 指标 | 原 Thinking 轮 | 本轮修复后 |','|---|---:|---:|',
        f'| 功能验收 | {s0["functional"]}/12 ({s0["functional"]/12:.2%}) | {s1["functional"]}/12 ({s1["functional"]/12:.2%}) |',
        f'| 完整流程 | {s0["workflow"]}/12 ({s0["workflow"]/12:.2%}) | {s1["workflow"]}/12 ({s1["workflow"]/12:.2%}) |',
        *[f'| {label} | {s0[key]} | {s1[key]} |' for label,key in [('协议重试','protocol_errors'),('实际读取','executed_reads'),('重复读取','duplicate_reads'),('工具总步数（含拒绝）','tool_steps'),('输入 Token','input_tokens'),('输出 Token（含思考）','output_tokens')]],
        f'| 平均工具步数 | {s0["mean_tool_steps"]:.2f} | {s1["mean_tool_steps"]:.2f} |',
        f'| 平均任务时长（秒） | {s0["elapsed_ms"]/12000:.3f} | {s1["elapsed_ms"]/12000:.3f} |','',
        f'本轮传输失败 {audited["summary"]["transport_failures"]} 次；安全事件/被拒绝越界见 audit.json 的逐次 tool 元数据与审批，未授权最终变更 {sum(not r["scope_valid"] or not r["protected_files_unchanged"] for r in audited["rows"])} 次。本轮 patch_file 错误/拒绝 {patch_failures} 次，涉及 {len(patch_failure_runs)} 个运行，其中最终完整流程通过 {recovered_patch_runs} 个；若未触发，则真实纠错效果不能据此推断，仅有专项自动化回归证据。','',
        '重复读取定义：同一运行中，read_file 的规范化 path/start/end 与完整成功输出均与先前相同，超出首次的次数；不把被拒绝调用算作实际读取。不等同于总工具步数。协议错误为 model_parsed.kind=retry；参数拒绝和测试失败另列。Token 仅为服务端可观测 usage；失败请求可能产生的未知用量不填零。','',
        '## 分题与失败归因','', '| 任务 | 原流程/2 | 新功能/2 | 新流程/2 |','|---|---:|---:|---:|']
    for task in sorted({r['task_id'] for r in analysis[-1]['rows']}):
        previous=[r for r in analysis[2]['rows'] if r['task_id']==task];current=[r for r in analysis[-1]['rows'] if r['task_id']==task]
        lines.append(f'| {task} | {sum(r["workflow_passed"] for r in previous)}/2 | {sum(r["functional_passed"] for r in current)}/2 | {sum(r["workflow_passed"] for r in current)}/2 |')
    failures=[a for a in attribution if a['category']!='success']
    lines+=['',f'失败分类：{dict(Counter(a["category"] for a in failures))}。']
    for a in failures:lines.append(f'- `{a["run_key"]}`：{a["category"]}；trace `{a["evidence"]}`；外部测试结果保存在对应 `external_final` 和独立 `rechecks`。')
    lines+=['','## 验证与证据','',
        f'- 全量自动化测试：{tests["tests"]} 条，失败 {tests["failures"]}，错误 {tests["errors"]}，跳过 {tests["skipped"]}；`full-tests.xml`。',
        f'- 新冻结 SHA256：`{report["freeze_sha256"]}`；每次新工作区和会话，48 次新旧运行会话 ID 无重复。',
        '- `audit.json`：12 次结果的独立外部复验、原生 tool_call_id 配对与思考回传摘要校验、36 次旧轨迹和本轮统计、Token/安全拒绝与失败证据。',
        '- `evidence.zip`：本轮完整可见模型/工具/传输记录、差异、工作区、冻结源代码、外部测试与独立复验。旧结果仍在 `../deepseek-official-2026-10-25/`，原 20×3 结果也保留。',
        '- 首个预检快照未发起 API 请求，因检查发现父子会话共享问题而被新冻结版本替代；未删除快照。开发中的新测试夹具失败、旧单元测试文案断言失败及中断检查均不计作通过，最终全量重跑单独保存；未修改评测验收测试。',
        '- 本轮只有 6 道已知题，修复是根据这些历史轨迹做的；不是独立留出评测，也不证明在新仓库上的泛化或统计显著提升。未保存原始思考正文，无法逐字重放历史 HTTP 请求；可按冻结代码和参数重新运行。','',
        '复跑需先检出本仓库，在独立副本中解压旧官方证据包和本轮证据包到根目录，再覆盖解压本轮批次内的 `source_snapshot.zip`，恢复匹配的运行时代码。保留仓库中的 v1 原始金标和证据归档，运行器会校验它们；另在该副本配置自己的 `.env.deepseek.local`（不得提交 Key）。冻结运行器默认输出目录已存在时拒绝覆盖，使用 `--output artifacts/your-new-run --freeze` 后再以同一 --output 执行 --run。先按根目录说明运行全量 pytest；验收器仍使用原始题目文件。','']
    (DEST/'report.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    print(json.dumps({'tests':tests,'comparison':{'before':s0,'after':s1},'report':str(DEST/'report.md')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
