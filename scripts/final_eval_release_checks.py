"""Post-run aggregation and custody checks. Does not alter frozen criteria or run results."""
from collections import Counter
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import load_json,write_json,digest,utcnow
from pico.evaluation.seven_dimensions import ratio
from scripts.run_final_agent_eval import DEFAULT as BATCH,ASSETS,check,events
from scripts.report_final_agent_eval import PUBLIC


def supplement():
    check(BATCH);audit=load_json(PUBLIC/'audit.json');rows=audit['rows'];freeze=load_json(BATCH/'freeze.json')
    assert all(datetime.fromisoformat(r['started_at'])>=datetime.fromisoformat(freeze['at']) for r in rows)
    old={t['id']:t for t in load_json(ROOT/'benchmarks/repair_suite_v1/tasks.json')['tasks']}
    tasks=load_json(ASSETS/'tasks.json')['tasks']
    for t in tasks:
        if t['cohort']=='existing':assert t['files']==old[t['id']]['files'] and t['editable_files']==old[t['id']]['editable_files']
    gold=load_json(ASSETS/'evidence_gold.json')
    for task in tasks:
        for span in gold[task['id']]['spans']:
            lines=task['files'][span['path']].splitlines()
            for line in span['lines']:assert lines[line['number']-1]==line['text']
    private=ROOT/'artifacts/final-private-gold-2026-10-25/holdout_references.json'
    assert digest(private)==freeze['private_gold_sha256']
    with zipfile.ZipFile(BATCH/'source_snapshot.zip') as archive:
        manifest=json.loads(archive.read('SOURCE_MANIFEST.json'))
        assert manifest==freeze['hashes']
        for name,h in manifest.items():assert hashlib.sha256(archive.read(name)).hexdigest()==h
        assert not any('final-private-gold' in n or n.endswith('holdout_references.json') for n in archive.namelist())
    groups={}
    for cohort in ('existing','holdout'):
        subset=[r for r in rows if r['cohort']==cohort]
        errors=[e for r in subset for e in r['metrics']['process']['failed_tool_recovery']]
        patches=[e for e in errors if e['name']=='patch_file']
        unexpected=[e for e in errors if not e['expected_initial_red_test']]
        logical={};persistence={};usage=[]
        for r in subset:
            for event in events(BATCH/'runs'/r['run_key']/'transport.jsonl'):
                logical.setdefault((r['run_key'],event['logical_request']),[]).append(event)
            for event in r['persistence_events']:
                persistence.setdefault((r['run_key'],event['temporary']),[]).append(event)
            for event in events(BATCH/'runs'/r['run_key']/'wire_io.jsonl'):
                if event['event']=='response':usage.append(json.loads(event['body']).get('usage') or {})
        failed_logical=[value for value in logical.values() if any(not e['ok'] for e in value)]
        groups[cohort]={
          'patch_rejections':len(patches),'same_file_patch_corrected':sum(e['same_file_patch_corrected'] for e in patches),
          'patch_correction_fraction':ratio(sum(e['same_file_patch_corrected'] for e in patches),len(patches)),
          'unexpected_tool_errors':len(unexpected),
          'later_same_tool_success_proxy':ratio(sum(e['later_same_tool_ok'] for e in unexpected),len(unexpected)),
          'transport_logical_requests_with_failures':len(failed_logical),
          'transport_recovered_requests':sum(any(e['ok'] for e in value) for value in failed_logical),
          'persistence_writes_with_retry_or_failure':len(persistence),
          'persistence_recovered_writes':sum(any(e['event']=='persistence_recovered' for e in value) for value in persistence.values()),
          'persistence_terminal_failures':sum(any(e['event']=='persistence_failed' for e in value) for value in persistence.values()),
          'reasoning_budget_observed_responses':sum(isinstance(u.get('completion_tokens_details',{}).get('reasoning_tokens'),int) for u in usage),
          'reasoning_tokens_exceed_requested_4096':sum(isinstance(u.get('completion_tokens_details',{}).get('reasoning_tokens'),int) and u['completion_tokens_details']['reasoning_tokens']>4096 for u in usage)}
    summary={'at':utcnow(),'kind':'Supplementary aggregation of frozen fields; primary metrics, criteria and raw run results unchanged.',
       'source_script_sha256':digest(Path(__file__)),'groups':groups,
       'checks':{'all_runs_started_after_freeze':True,'original_twenty_files_and_scopes_unchanged':True,
                 'gold_lines_match_original_faulty_source':True,'private_gold_hash_unchanged':True,
                 'source_snapshot_matches_frozen_hashes':True,'private_references_excluded_from_snapshot':True},
       'timing_definition':'elapsed_ms is worker start through entry to finalization, before external verification. External verifier elapsed_ms is stored separately. Not a total end-to-end latency including audit.',
       'recovery_limits':'Initial expected red tests excluded from unexpected tool errors. Later same-tool success is a proxy, not causal attribution. Patch fraction follows the pre-frozen same-file criterion; no denominator yields null.'}
    write_json(PUBLIC/'supplementary-metrics.json',summary,exclusive=True)
    text=(PUBLIC/'report.md').read_text('utf-8')
    text+='\n## 恢复汇总与实现口径核对\n\n'
    text+='以下只汇总冻结逐次字段，不修改金标、原始结果或主指标。首次预期失败测试不算异常；“后续同工具成功”只作代理，不能证明因果恢复。\n\n'
    for cohort,g in groups.items():text+=f'- {cohort}：{g}\n'
    text+='\n时长口径澄清：elapsed_ms记录worker开始到进入最终整理，尚未包含外部验证；外部验证耗时单独记录。不将其称为包含完整独立审计的端到端耗时。框架、用量、模型版本差异仍使历史耗时不构成严格性能对照。\n'
    text+='\n恢复比率及校验源代码、分母见supplementary-metrics.json与scripts/final_eval_release_checks.py；脚本为发布期聚合，原始冻结运行器与主评测定义未变。\n'
    (PUBLIC/'report.md').write_text(text,encoding='utf-8',newline='\n')
    print(json.dumps(summary,ensure_ascii=False))


def validate():
    check(BATCH);archive_path=PUBLIC/'evidence.zip'
    with zipfile.ZipFile(archive_path) as archive:
        manifest=json.loads(archive.read('EVIDENCE_MANIFEST.json'))
        for name,h in manifest['sha256'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest()==h,name
            assert digest(ROOT/name)==h,name
    hashes=load_json(PUBLIC/'run-evidence-manifest.json')
    for files in hashes.values():
        for name,h in files.items():assert digest(ROOT/name)==h,name
    write_json(PUBLIC/'release-validation.json',{'at':utcnow(),'frozen_source_and_assets_unchanged':True,
       'all_recorded_run_files_unchanged':True,'archive_sha256':digest(archive_path),
       'archive_entries':len(manifest['sha256']),'credential_scan':manifest['credential_scan'],
       'private_references_excluded':manifest['private_reference_solutions_excluded']},exclusive=True)
    print('Release hashes and original run files verified.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--validate',action='store_true');args=p.parse_args()
    validate() if args.validate else supplement()
