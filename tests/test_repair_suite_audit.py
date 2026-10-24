"""Audit-only unit cases. These are not real model evaluation runs."""
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('audit_repair_suite',Path(__file__).resolve().parents[1]/'scripts/audit_repair_suite.py')
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

def test_failed_request_makes_run_token_usage_incomplete():
    calls=[{'event':'request','index':1},
           {'event':'response','index':1,'usage':{'input_tokens':20,'output_tokens':3}},
           {'event':'request','index':2}, {'event':'error','index':2,'error':'timeout'}]
    summary=audit.observed_usage(calls)
    assert summary['input_tokens']['observed_total']==20
    assert summary['input_tokens']['observed_calls']==1
    assert not summary['input_tokens']['complete']

def test_all_provider_usage_present_is_complete():
    calls=[{'event':'request','index':1},{'event':'response','index':1,'usage':{'input_tokens':20,'output_tokens':0}}]
    summary=audit.observed_usage(calls)
    assert summary['input_tokens']['complete']
    assert summary['output_tokens']['complete']

def test_no_reply_or_missing_usage_remains_unknown():
    assert not audit.observed_usage([{'event':'request'}])['input_tokens']['complete']
    assert not audit.observed_usage([{'event':'request'},{'event':'response','usage':{}}])['input_tokens']['complete']
