"""Audit completed API probes and publish a transport-only diagnosis."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.diagnose_api_timeout import check, read, save, sha

OUTPUT=ROOT/'artifacts/api-timeout-diagnosis-2026-10-25'
PUBLIC=ROOT/'benchmarks/results/api-timeout-diagnosis-2026-10-25'


def timings(row):
    sent=row.get('request_sent',0)
    return {key:row[key]-sent for key in ('headers_received','first_byte','first_reasoning','first_visible') if key in row}


def historical_summary(rows):
    result={'logical_requests':len(rows),'completed_responses':sum(r['ok'] for r in rows),
        'final_errors':sum(not r['ok'] for r in rows),
        'transport_attempts':sum(r['usage']['transport_attempts'] for r in rows),
        'failed_transports':sum(r['usage']['transport_failures'] for r in rows), 'length_bins':[], 'overlap_bins':[]}
    for lo,hi in ((0,8000),(8000,16000),(16000,32000),(32000,10000000)):
        group=[r for r in rows if lo<=r['chars']<hi]
        result['length_bins'].append({'chars_from':lo,'chars_to_exclusive':hi,'requests':len(group),'errors':sum(not r['ok'] for r in group)})
    for overlap in range(3):
        group=[r for r in rows if r['other_logical_requests_at_start']==overlap]
        result['overlap_bins'].append({'other_requests_at_start':overlap,'requests':len(group),'errors':sum(not r['ok'] for r in group)})
    result['failure_prompt_chars']=[r['chars'] for r in rows if not r['ok']]
    result['successful_prompt_chars_range']=[min(r['chars'] for r in rows if r['ok']),max(r['chars'] for r in rows if r['ok'])]
    return result


def build():
    check(OUTPUT)
    completed=read(OUTPUT/'results.json');specs={r['id']:r for r in read(OUTPUT/'cases.json')}
    rows=completed['rows']
    assert completed['completed'] and len(rows)==len(specs)==25
    assert len({r['id'] for r in rows})==25
    for row in rows:
        directory=OUTPUT/'runs'/row['id']
        assert row==read(directory/('result.json' if (directory/'result.json').exists() else 'deadline.json'))
        assert row['prompt_sha256']==specs[row['id']]['prompt_sha256']
        assert row['payload_sha256']==specs[row['id']]['payload_sha256']
        if row['ok']:
            assert row['http_status']==200 and row.get('usage')
        if row['kind'].startswith('padding') and row['ok']:
            assert row['content'].strip()=='OK'
    historical=historical_summary(read(OUTPUT/'historical_requests.json'))
    groups=[]
    for kind in ('padding128','padding8192','padding32768'):
        for concurrency in (1,3):
            group=[r for r in rows if r['kind']==kind and r['concurrency']==concurrency]
            successful=[r for r in group if r['ok']]
            item={'kind':kind,'concurrency':concurrency,'requests':len(group),'completed':len(successful)}
            for field in ('headers_received','first_reasoning','first_visible'):
                values=[timings(r)[field] for r in successful if field in r]
                item[field+'_median_ms']=statistics.median(values) if values else None
            item['elapsed_median_ms']=statistics.median(r['elapsed_ms'] for r in successful) if successful else None
            item['prompt_tokens']=[r['usage']['prompt_tokens'] for r in successful]
            item['cached_tokens']=[r['usage'].get('prompt_tokens_details',{}).get('cached_tokens') for r in successful]
            groups.append(item)
    real=[]
    for row in rows:
        if row['kind']!='real':
            continue
        usage=row.get('usage') or {}
        item={k:row.get(k) for k in ('id','group','concurrency','stream','ok','http_status','elapsed_ms','started_at','finished_at','prompt_sha256','payload_sha256',
            'phase','error_type','error','trace_id','response_headers','reasoning_chars','visible_chars','finish_reason')}
        item.update(timings_from_request_sent_ms=timings(row),usage=usage,usage_is_final=bool(row['ok']),
            tokens_over_3072=usage.get('completion_tokens',0)>3072)
        events=[json.loads(l) for l in (OUTPUT/'runs'/row['id']/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        progress=[e for e in events if e['event']=='stream_progress']
        item['last_stream_progress_ms']=progress[-1]['elapsed_ms'] if progress else None
        end=row.get('first_visible') or item['last_stream_progress_ms']
        thought_tokens=usage.get('completion_tokens_details',{}).get('reasoning_tokens')
        if row['stream'] and isinstance(thought_tokens,int) and end and 'first_reasoning' in row and end>row['first_reasoning']:
            item['approx_client_observed_reasoning_tokens_per_second']=thought_tokens/((end-row['first_reasoning'])/1000)
        real.append(item)
    summary={'scope':'Transport diagnostics only, not task success rates. One public failing prompt repeated.',
        'freeze_sha256':sha((OUTPUT/'freeze.json').read_bytes()),'historical':historical,
        'planned_requests':25,'completed_requests':sum(r['ok'] for r in rows),'http_status_counts':dict(Counter(str(r.get('http_status')) for r in rows)),
        'synthetic_groups':groups,'real_replays':real,'local_timeout_lab':read(OUTPUT/'local-timeout-lab.json'),
        'limitations':read(OUTPUT/'protocol.json')['limitations']+[
            'This is one account and time window; provider queue/server load is not exposed.',
            'Synthetic groups happened to run all serial before all parallel; time and cache are confounders.',
            'Historical requests did not record first-byte timing. New replays cannot identify every historical timeout phase.',
            'Diagnostic direct HTTPS client and User-Agent differ from runtime; only within-diagnostic comparisons are controlled.',
            'Streaming exposes generation progress; it does not make inference faster or guarantee completion.',
            'Reasoning text is counted but never retained. Deadline usage may be a partial cumulative counter, not final billing.']}
    phase_summary={}
    for name,before,after in [('dns','worker_started','dns_done'),('tcp','dns_done','tcp_connected'),
            ('tls','tcp_connected','tls_connected'),('headers_wait','request_sent','headers_received')]:
        values=[r[after]-r[before] for r in rows if before in r and after in r]
        phase_summary[name]={'observations':len(values),'median_ms':statistics.median(values),'maximum_ms':max(values)} if values else None
    summary['network_phases']=phase_summary
    summary['real_serial_stream']=dict(Counter('completed' if r['ok'] else r.get('error_type') or 'incomplete'
        for r in real if r['stream'] and r['concurrency']==1))
    summary['real_parallel_stream']=dict(Counter('completed' if r['ok'] else r.get('error_type') or 'incomplete'
        for r in real if r['stream'] and r['concurrency']==3))
    PUBLIC.mkdir(parents=True,exist_ok=False)
    save(PUBLIC/'summary.json',summary)
    lines=['# API超时、长度与并发诊断','',
        '2026-10-25（Asia/Shanghai）。原始60次基线和12次定向回归均未修改。',
        '本轮冻结25次独立传输诊断：18次固定填充文本对照，7次重放同一个已公开失败请求。没有执行模型返回的工具，没有新代码修复任务，也没有自动重试。',
        f"冻结SHA256：`{summary['freeze_sha256']}`。所有请求均保留。",'',
        '## 主要判断','',
        '可观察证据应分层解读：输入是否超出限制、请求是否迟迟不开始生成、开始生成后是否持续思考、以及客户端等待是否覆盖了整个生成阶段。服务端内部排队时长没有直接暴露，以下结论只覆盖本轮样本。',
        f"- 合成对照完成{sum(g['completed'] for g in groups)}/18；真实流式串行结果为{summary['real_serial_stream']}，三路结果为{summary['real_parallel_stream']}。",
        f"- 真实流式请求发送后首思考时间为{min(timings(r)['first_reasoning'] for r in rows if r['kind']=='real' and r['stream'] and 'first_reasoning' in r)/1000:.3f}–{max(timings(r)['first_reasoning'] for r in rows if r['kind']=='real' and r['stream'] and 'first_reasoning' in r)/1000:.3f}秒；其后的长时间等待发生在生成阶段。",
        '- 本地复现确认socket等待和2倍重试预算不是严格总时限。流式能观察进度，但仍需要独立的任务总截止。',
        '- 原记录的180秒读取超时不是一个服务端返回的HTTP超时码，不能据此声称服务端宕机。具体生成速度波动、模型深思长度与后台负载的贡献仍无法完全分离。','',
        '## 旧日志能说明什么','',
        f"12次定向回归包含{historical['logical_requests']}个逻辑模型请求，{historical['completed_responses']}次返回完整响应、{historical['final_errors']}次最终错误；共{historical['transport_attempts']}次传输，{historical['failed_transports']}次传输失败。",
        f"失败请求长度为{min(historical['failure_prompt_chars'])}–{max(historical['failure_prompt_chars'])}字符；成功请求最大{historical['successful_prompt_chars_range'][1]}字符。没有一个明确的输入长度硬阈值可以解释失败。",
        '7个最终错误均发生在另有2个逻辑请求进行时；但105个请求中92个本就处于这种状态，串行样本不足，不能据此证明三路并发导致超时。旧记录没有连接、首字节或首Token分段时间，不能把读取超时直接称为服务端排队。','',
        '这些最终错误均经过2次传输尝试、累计等待约360秒。重试重新POST同一个完整请求，客户端没有推理续传机制；无法保证前一次断开后服务端已经停止计算。','',
        '## 固定输出的输入长度与并发对照','',
        '各组3次，统一模型、temperature=0.2、stream=true、max_tokens=64，要求只输出OK。表中响应头和首思考时间从请求发送完成计，完整时间从worker开始计。',
        '| 输入字符 | 并发 | 完成/3 | 响应头中位秒 | 首思考中位秒 | 完整中位秒 |',
        '|---:|---:|---:|---:|---:|---:|']
    for group in groups:
        fmt=lambda key: f"{group[key]/1000:.3f}" if group.get(key) is not None else '—'
        lines.append(f"| {group['kind'][7:]} | {group['concurrency']} | {group['completed']}/3 | {fmt('headers_received_median_ms')} | {fmt('first_reasoning_median_ms')} | {fmt('elapsed_median_ms')} |")
    lines+=['','这些小样本只能检查本轮是否出现长度硬失败或明显排队，不能证明任意输入长度、任意负载都安全。更长填充文本内容很简单，不能代表代码修复推理；重复前缀会命中缓存，时间顺序也有混杂。具体输入与缓存Token见summary.json。','',
        '## 同一个真实失败提示词：串行、并发与非流式','',
        '重放来源为已公开v2证据中的 `10-boltons_xfrange-r1` 第6个请求，8356字符。流式串行3次、三路并发3次；另外非流式串行1次。参数与该历史请求相同，仅传输模式/独立诊断客户端不同。响应头与首思考时间从请求发送完成计。','',
        '| 请求 | 模式/并发 | 结果 | 响应头秒 | 首思考秒 | 首可见秒 | 完整/截止秒 | 思考Token（可能部分） |',
        '|---|---|---|---:|---:|---:|---:|---:|']
    for row in real:
        t=row['timings_from_request_sent_ms'];fmt=lambda key:f"{t[key]/1000:.3f}" if key in t else '—'
        tokens=row['usage'].get('completion_tokens_details',{}).get('reasoning_tokens','未知')
        status='完成' if row['ok'] else row.get('error_type') or '未完成'
        lines.append(f"| {row['id']} | {'流式' if row['stream'] else '非流式'}/{row['concurrency']} | {status} | {fmt('headers_received')} | {fmt('first_reasoning')} | {fmt('first_visible')} | {row['elapsed_ms']/1000:.3f} | {tokens} |")
    rates=[r['approx_client_observed_reasoning_tokens_per_second'] for r in real if 'approx_client_observed_reasoning_tokens_per_second' in r]
    lines+=['','注意：完成表示一次HTTP模型响应结束，不表示修复任务完成；可见回复仅保存、不执行。HardDeadline表示240秒外部截止，非流式socket等待180秒。被截止的部分usage不代表完整计费。',
        f"根据provider累计思考Token及收到数据的时间，客户端可见的平均思考速率约{min(rates):.1f}–{max(rates):.1f} Token/秒。它混合了生成、传输和处理时间，不是纯GPU解码速率；同样长度的输入仍有思考长度和速度双重波动。",'',
        '## 连接阶段计时','',
        '| 阶段 | 观测数 | 中位秒 | 最大秒 |','|---|---:|---:|---:|']
    for name,value in phase_summary.items():
        if value:
            lines.append(f"| {name} | {value['observations']} | {value['median_ms']/1000:.3f} | {value['maximum_ms']/1000:.3f} |")
    lines+=['','headers_wait包含网络、入口调度及开始返回响应头前的处理，不能等同于纯队列时间。非流式响应头可能等到整段生成结束才返回，需结合流式首Token判断。','',
        '## 超时配置的本地复现','',
        '使用127.0.0.1标准库HTTP服务，不调用模型。现有urllib客户端的等待限制约束阻塞连接/读取，重试预算仅在发起下一次尝试前检查。',
        '| 场景 | socket等待秒 | 实际秒 | 结果 |','|---|---:|---:|---|']
    for row in summary['local_timeout_lab']['rows']:
        lines.append(f"| {row['case']} | {row['timeout_seconds']} | {row['elapsed_ms']/1000:.3f} | {'成功' if row['ok'] else row['error_type']+'（'+row['phase']+'）'} |")
    lines+=['','持续每0.1秒发送数据的响应可以超过0.4秒socket等待和0.8秒名义重试预算后成功返回。因此当前2倍预算不是严格墙钟截止。长非流式生成没有数据到达时会触发读取超时；流式持续思考则可能长期不触发它。','',
        '## 判断边界与后续配置','',
        '- 结合分段时序区分“未开始输出”与“持续推理但没有可见回复”。首Token快且持续有思考数据，支持生成阶段耗时，不能称为连接故障或分钟级排队。',
        '- 没有429不等于没有服务端调度；账户RPM/TPM与共享集群队列不同。没有服务端日志，无法排除所有服务端负载影响。',
        '- 建议分开设置连接、输出空闲和任务总截止，保留provider trace ID。流式只用于接收进度，必须等待完整响应后才解释或执行工具。',
        '- `max_tokens=3072`不限制该平台的思考Token。模型上游支持reasoning_effort，但硅基流动文档对该模型的兼容性未明确，本轮没有试验或自动改成low；不能宣称参数有效。',
        '- 如果需要稳定演示，先限制客户端并发到1降低变量；这是一项保守配置，不能宣称本轮已证明三并发是根因。不要继续仅靠延长等待及重试来掩盖长期推理。','',
        '## 官方资料','',
        '- [硅基流动Chat Completions](https://docs.siliconflow.cn/docs/api/chat-completions-post)：max_tokens不含思考Token，X-Trace-Id与响应trace ID。',
        '- [硅基流动Reasoning](https://docs.siliconflow.cn/docs/userguide/capabilities/reasoning)：thinking_budget并非对所有模型硬截断。',
        '- [硅基流动Rate Limits](https://docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation)：账号/模型级RPM、TPM与429。',
        '- [GLM-5.3-Flash上游模型卡](https://huggingface.co/zai-org/GLM-5.3-Flash)：reasoning_effort默认max，支持low/high/max；不代表托管平台必然透传。',
        '- [Python urllib.request](https://docs.python.org/3.10/library/urllib.request.html)：timeout用于阻塞操作。','',
        '完整限制条件、缓存值、部分用量和provider trace ID见summary.json。证据包保留计划、原始请求、分段事件、结果与本地复现。未保存思考正文。','',
        '复现会产生真实模型调用：先将v2证据包中的 `artifacts/repair-regression-v2-2026-10-24/` 目录解压到项目根目录，其余源码无需覆盖；仓库中的原v2证据zip也需保留。然后在独立输出目录运行 `python scripts/diagnose_api_timeout.py --prepare --output artifacts/new-api-diagnosis`，再运行同脚本 `--run --output artifacts/new-api-diagnosis`。脚本核对原模型I/O和已公开zip哈希，禁止覆盖已有计划/结果。','']
    (PUBLIC/'report.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    (PUBLIC/'diagnostic-tests.xml').write_bytes((OUTPUT/'diagnostic-tests.xml').read_bytes())
    print(json.dumps({'requests':25,'completed':summary['completed_requests'],'public':str(PUBLIC)},ensure_ascii=False))


def export():
    paths={p.relative_to(ROOT).as_posix():p for p in OUTPUT.rglob('*') if p.is_file()}
    for name in ('scripts/diagnose_api_timeout.py','scripts/diagnose_timeout_semantics.py',
                 'scripts/report_api_diagnosis.py','tests/test_api_diagnosis.py'):
        paths[name]=ROOT/name
    for path in PUBLIC.iterdir():
        if path.is_file() and path.suffix not in {'.zip','.sha256'}:
            paths[path.relative_to(ROOT).as_posix()]=path
    secrets=[]
    for line in (ROOT/'.env').read_text(encoding='utf-8').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key,value=line.split('=',1);value=value.strip().strip('\"\'')
        if len(value)>=8 and any(word in key.upper() for word in ('KEY','SECRET','TOKEN','PASSWORD')):
            secrets.append(value.encode())
    hashes={}
    for name,path in paths.items():
        data=path.read_bytes()
        assert not path.name.startswith('.env'), name
        assert not any(secret in data for secret in secrets), 'Credential in '+name
        assert not re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}',data),name
        hashes[name]=sha(data)
    destination=PUBLIC/'evidence.zip'
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,path in sorted(paths.items()):
            archive.write(path,name)
        archive.writestr('EVIDENCE_MANIFEST.json',json.dumps({'sha256':hashes,'credential_scan':'passed',
            'base_runtime_commit':'823a11a5f1a0ae43aa57f9f0bd45c7511442e691'},indent=2))
    (PUBLIC/'evidence.sha256').write_text(sha(destination.read_bytes())+'  evidence.zip\n',encoding='ascii',newline='\n')
    print(json.dumps({'files':len(paths),'bytes':destination.stat().st_size,'sha256':sha(destination.read_bytes()),'credential_scan':'passed'}))


if __name__=='__main__':
    build()
    export()
