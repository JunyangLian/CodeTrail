import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from pico.evaluation.repair_suite import (CATEGORIES, ROOT, SUITE, PROTOCOL, RecordingClient,
    aggregate, classify, install, inventory, safe_path, trace_metrics, verify_pristine)
from pico.providers.clients import FakeModelClient

def manifest():
    return json.loads((SUITE/'tasks.json').read_text(encoding='utf-8'))

def test_suite_independent_tasks_categories_and_real_provenance():
    tasks=manifest()['tasks']
    assert len(tasks)==len({t['id'] for t in tasks})==20
    assert Counter(t['category'] for t in tasks)==CATEGORIES
    upstream=[t for t in tasks if t['origin']['kind']=='upstream_minimal_reproduction']
    assert len(upstream)==8
    assert len({t['origin']['repository'] for t in upstream})==2
    for task in upstream:
        origin=task['origin']
        assert len(origin['fix_commit'])==len(origin['buggy_commit'])==40
        assert origin['fix_commit']!=origin['buggy_commit']
        directory=SUITE/'sources'/task['id']
        for prefix,key in [('buggy','buggy_module_sha256'),('fixed','fixed_module_sha256')]:
            raw=(directory/f"{prefix}-{Path(origin['module']).name}").read_bytes()
            assert hashlib.sha256(raw).hexdigest()==origin[key]
        assert 'UPSTREAM_LICENSE.txt' in task['files']

def test_legacy_tasks_and_gold_are_preserved():
    old=json.loads((ROOT/'benchmarks/repair_validation_tasks.json').read_text(encoding='utf-8'))['tasks']
    current={t['id']:t for t in manifest()['tasks']}
    for task in old:
        assert {name:current[task['id']]['files'][name] for name in task['files']}==task['files']
    for task in current.values():
        assert task['sha256']=={name:hashlib.sha256(text.encode()).hexdigest() for name,text in task['files'].items()}

def test_fixture_never_contains_references_or_fix_metadata(tmp_path):
    task=manifest()['tasks'][0]
    install(tmp_path/'work',task['files'])
    assert inventory(tmp_path/'work')==task['sha256']
    assert not list((tmp_path/'work').rglob('*reference*'))
    assert not (tmp_path/'work'/'tasks.json').exists()
    assert not (tmp_path/'work'/'freeze.json').exists()
    assert not (tmp_path/'work'/'.env').exists()
    with pytest.raises(FileExistsError):
        install(tmp_path/'work',task['files'])
    with pytest.raises(ValueError):
        safe_path(tmp_path,'../escape.py')

def test_pristine_verifier_uses_original_tests_and_no_skips(tmp_path):
    task=manifest()['tasks'][0]
    refs=json.loads((SUITE/'reference_solutions.json').read_text(encoding='utf-8'))[task['id']]
    buggy=verify_pristine(task,{},tmp_path/'buggy')
    fixed=verify_pristine(task,{k:v.encode() for k,v in refs.items()},tmp_path/'fixed')
    assert not buggy['accepted'] and buggy['tests_run']==8
    assert fixed['accepted'] and fixed['tests_run']==8 and fixed['skipped']==0
    with pytest.raises(ValueError):
        verify_pristine(task,{'test_acceptance.py':b'pass'},tmp_path/'invalid')

def test_workflow_requires_successful_test_after_latest_write():
    test={'event':'tool_executed','name':'run_shell','args':{'command':PROTOCOL['verify_command']},'exit_code':0,'tool_status':'ok'}
    patch={'event':'tool_executed','name':'patch_file','workspace_changed':True,'tool_status':'ok'}
    assert not trace_metrics([test,patch],{}, {})['agent_verified_after_last_write']
    assert trace_metrics([patch,test],{}, {})['agent_verified_after_last_write']
    assert not trace_metrics([],{}, {})['agent_verified_after_last_write']

def test_token_usage_missing_is_not_reported_as_complete_zero():
    events=[{'event':'model_parsed','completion_metadata':{'input_tokens':20,'output_tokens':4}},
            {'event':'model_parsed','completion_metadata':{}}]
    value=trace_metrics(events,{}, {})
    assert value['input_tokens']['observed_total']==20
    assert value['input_tokens']['observed_calls']==1
    assert not value['input_tokens']['complete']

def test_aggregation_keeps_failed_runs_and_counts_tasks_once():
    rows=[{'task_id':'a','category':'single_file','functional_passed':True,'workflow_passed':True,'failure_category':'success'},
          {'task_id':'a','category':'single_file','functional_passed':False,'workflow_passed':False,'failure_category':'no_code_change'},
          {'task_id':'a','category':'single_file','functional_passed':True,'workflow_passed':False,'failure_category':'missing_reverification_or_incomplete_workflow'}]
    result=aggregate(rows)
    assert result['unique_tasks']==1 and result['runs']==3
    assert result['functional_rate']==2/3 and result['workflow_rate']==1/3
    assert result['failure_categories']['no_code_change']==1

def test_recording_client_retains_full_prompt_reply_and_redacts_secrets(tmp_path,monkeypatch):
    monkeypatch.setenv('PICO_OPENAI_API_KEY','evaluation-private-fixture-secret')
    client=RecordingClient(FakeModelClient(['<final>'+('x'*5000)+'</final>']),tmp_path/'model.jsonl')
    text=client.complete('hello evaluation-private-fixture-secret',3072)
    assert len(text)>5000
    lines=[json.loads(line) for line in (tmp_path/'model.jsonl').read_text(encoding='utf-8').splitlines()]
    assert '<redacted>' in lines[0]['prompt']
    assert lines[1]['raw_text']==text

def test_failure_attribution_keeps_infrastructure_distinct():
    assert classify({'error':'HTTP failure'})=='model_or_runtime_error'
    assert classify({'controller_timeout':True})=='run_deadline'
    assert classify({'scope_valid':True,'changed_files':[]})=='no_code_change'
