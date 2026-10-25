"""Seal measured v2 regression evidence; never overwrite historical evaluation assets."""
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.config import _parse_env_line
from pico.evaluation.repair_suite import load_json,write_json,digest,utcnow

PUBLIC=ROOT/'benchmarks/results/reliability-v2-2026-10-25'
RAW=ROOT/'artifacts/reliability-v2-2026-10-25'


def main():
    custody=load_json(RAW/'historical-custody.json')
    for name,h in custody['historical_files'].items():assert digest(ROOT/name)==h,name
    tests=list(ET.parse(RAW/'full-tests.xml').getroot().iter('testsuite'))
    counts={k:sum(int(s.get(k,0)) for s in tests) for k in ('tests','failures','errors','skipped')}
    assert counts['failures']==counts['errors']==0
    guard=load_json(RAW/'frozen-guard-final/results.json');network=load_json(RAW/'network-listeners-final/results.json')
    initial=load_json(RAW/'os-security/results.json');recall=load_json(PUBLIC/'recall-diagnosis.json')
    assert guard['passed']==guard['evaluated']==14 and network['passed'] and not initial['passed']
    assert all(x['host_controls_passed'] for x in [network])
    report={'at':utcnow(),'software_tests':counts,'frozen_guards':{'passed':guard['passed'],'evaluated':guard['evaluated'],'backend':'AppContainer'},
      'controlled_loopback_network':network,'initial_errno_probe_passed':False,'historical_reports_unchanged':True,
      'historical_files_verified':len(custody['historical_files']),'recall':recall['groups'],
      'model_calls_this_round':0,'new_repair_tasks':0,'new_repair_success_rate':None,
      'limits':['No model efficacy, protocol-error reduction, Recall improvement or cost decrease experiment.',
       'No corrected replacement gold; legacy53.21% retained.',
       'Host backend still unisolated; optional AppContainer only supports stdlib unittest verifier.',
       'AppContainer shares Windows kernel; Internet egress, kernel escapes and disk hard quotas unassessed.']}
    write_json(PUBLIC/'audit.json',report,exclusive=True)
    lines=['# CodeTrail：Recall诊断与OS隔离回归','',
      '本轮只做实现、轨迹诊断与回归。没有新增修复任务、调用模型或重跑留出。历史主指标、金标、144条运行和27份历史发布文件均未改变。','',
      '## Recall诊断','',
      '| 观察切片 | 命中/分母 | 旧Recall@5 |','|---|---:|---:|']
    for name,g in recall['groups'].items():
        r=g['legacy_recall_at_5'];lines.append(f'| {name} | {r["numerator"]}/{r["denominator"]} | {r["value"]*100:.2f}% |')
    lines+=['','总体53.21%混有54次余额中断；无余额切片81.55%只是描述性诊断，未替换主分母。四道boltons题把editable范围误标为必要文件，config/transport/validation无测试静态导入路径；只发布标注勘误，不事后改金标重算高分。14条未覆盖观察、缺失文件与后读范围见[诊断JSON](recall-diagnosis.json)和[逐次CSV](retrieval-misses.csv)。',
      '', '该指标是Agent前5个去重访问文件的覆盖，不是独立检索器排名质量。两次dotenv_override遗漏冻结契约文件虽功能通过，也不证明证据完整。本轮只加入符号搜索/范围阅读提示，未证明Recall提升。','',
      '## 隔离与可靠性实现','',
      '- 每次零能力AppContainer、私有过滤副本和标准库，无宿主挂载、无API环境继承；核验内核令牌后恢复进程，不改既有ACL。',
      '- Job限制单进程/256MiB，超时和64KiB输出洪泛终止；拒绝链接与超限快照；系统过滤服务不可用时拒绝，绝不退回宿主Shell。',
      '- 固定精确unittest命令和原审批。新后端相同14个守卫14/14；旧13/14不变。正常计算/宿主读写/凭据/内存/输出洪泛分别回归。',
      '- 真实本地TCP/UDP阳性监听器仅收到HOST_CONTROL，没有SANDBOX_PROBE；只证明受控本地网络边界，外网出口未评估。',
      '- 首次8项错误码型探针失败（6通过、1失败、1错误）完整保留。UDP connect不等于投递，TCP阻断也可能超时；后续另立实际投递协议，不宣称初次8/8通过。',
      '- 余额/鉴权/模型ID不符停止新派发；测试验证首条402后89槽位未启动，失败不覆盖。',
      '- 原生schema显示真实timeout/精确隔离命令，剩余步提示与执行层最后一步禁写保留验证；多调用仍严格拒绝，没有扩大工具权限。','',
      '## 测试与边界','',f'全量软件回归：{counts}。这些是软件测试，不是独立Agent修复任务。',
      '', 'host兼容后端仍没有OS隔离。AppContainer共享Windows内核，不是VM，不承诺任意系统资源不可读；没有依赖包/多进程支持、磁盘硬配额或全面外网验证。模型协议/Token/修复率没有新实验，不报告提高。',
      '', '## 复现与证据','',
      '- [完整说明与命令](../../../docs/reliability-v2.md)。专用Demo默认AppContainer；通用网页显式使用--shell-backend appcontainer，host模式界面标注无隔离。',
      '- [证据包](evidence.zip)、[SHA256](evidence.sha256)保存原始失败探针、后续独立网络协议、固定守卫、源码和全量JUnit。',
      '- 历史实验请用d2bb48c干净副本或历史source_snapshot；v2源码不会伪造匹配旧freeze。历史13/14、53.21%以及原失败仍可核对。','']
    (PUBLIC/'report.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    files={}
    for p in RAW.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix.lower() in {'.json','.jsonl','.xml','.py'}:
            files[p.relative_to(ROOT).as_posix()]=p
    for pattern in ('pico/**/*.py','tests/*.py','scripts/*v2*.py','scripts/check_appcontainer*.py','scripts/check_isolation_guard_regression.py',
                    'scripts/run_final_agent_eval.py','scripts/run_final_demo.py','docs/reliability-v2.md','docs/final-agent-*.md','README.md','pico/web_assets/app.js'):
        for p in ROOT.glob(pattern):
            if p.is_file():files[p.relative_to(ROOT).as_posix()]=p
    for p in PUBLIC.iterdir():
        if p.is_file():files[p.relative_to(ROOT).as_posix()]=p
    secrets={v.encode() for k,v in os.environ.items() if len(v)>=8 and any(w in k.upper() for w in ('API_KEY','TOKEN','SECRET','PASSWORD'))}
    for d in (ROOT,ROOT.parent/'pico-main'):
        for p in d.glob('.env*'):
            if p.is_file() and p.name!='.env.example':
                for line in p.read_text('utf-8').splitlines():
                    item=_parse_env_line(line)
                    if item and len(item[1])>=8 and any(w in item[0].upper() for w in ('API_KEY','TOKEN','SECRET','PASSWORD')):secrets.add(item[1].encode())
    hashes={}
    for name,p in sorted(files.items()):
        assert not p.name.startswith('.env') and 'final-private-gold' not in name
        assert not any(secret in p.read_bytes() for secret in secrets),'Credential in '+name
        hashes[name]=digest(p)
    dest=PUBLIC/'evidence.zip'
    with zipfile.ZipFile(dest,'x',zipfile.ZIP_DEFLATED) as archive:
        for name,p in sorted(files.items()):archive.write(p,name)
        archive.writestr('MANIFEST.json',json.dumps({'at':utcnow(),'sha256':hashes,'credential_scan':'passed','historical_custody':custody},indent=2))
    with zipfile.ZipFile(dest) as archive:
        for name,h in hashes.items():assert hashlib.sha256(archive.read(name)).hexdigest()==h
    (PUBLIC/'evidence.sha256').write_text(digest(dest)+'  evidence.zip\n',encoding='ascii',newline='\n')
    print(json.dumps({'tests':counts,'guard_passes':14,'bytes':dest.stat().st_size,'sha256':digest(dest),'historical_unchanged':True}))


if __name__=='__main__':main()
