import io
import json
import urllib.error
from unittest.mock import patch

import pytest
from pico.providers.clients import OpenAICompatibleModelClient

class Response:
    headers={'Content-Type':'application/json','x-request-id':'public-request-id'}
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self):
        return json.dumps({'choices':[{'message':{'content':'<final>ok</final>'}}],
                           'usage':{'prompt_tokens':8,'completion_tokens':2}}).encode()

def client():return OpenAICompatibleModelClient('test','https://example.com/v1','test-key',.2,30,'chat_completions')

def test_direct_read_timeout_retries_same_model_request_and_preserves_unknown_usage():
    c=client();record=[];c.on_transport_event=record.append
    with patch('urllib.request.urlopen',side_effect=[TimeoutError('read timed out'),Response()]) as request, patch('time.sleep'):
        assert c.complete('prompt',16)=='<final>ok</final>'
    assert request.call_count==2
    assert request.call_args_list[0].args[0].data==request.call_args_list[1].args[0].data
    assert len(record)==2 and record[0]['will_retry']
    assert c.last_completion_metadata['unknown_usage_attempts']==1
    assert not c.last_completion_metadata['transport_usage_complete']
    assert c.last_completion_metadata['input_tokens']==8
    assert record[1]['request_id']=='public-request-id'
    assert 'test-key' not in str(record)

def test_timeout_retries_are_bounded_and_error_keeps_telemetry():
    c=client()
    with patch('urllib.request.urlopen',side_effect=TimeoutError('read timed out')) as request, patch('time.sleep'):
        with pytest.raises(TimeoutError,match='3 transport attempts'):c.complete('prompt',16)
    assert request.call_count==3
    assert c.last_completion_metadata['transport_failures']==3
    assert 'input_tokens' not in c.last_completion_metadata

def test_authentication_error_is_not_retried():
    c=client()
    error=urllib.error.HTTPError('https://example.com',401,'Unauthorized',{},io.BytesIO(b'bad key'))
    with patch('urllib.request.urlopen',side_effect=error) as request:
        with pytest.raises(RuntimeError,match='401'):c.complete('prompt',16)
    assert request.call_count==1
    assert c.last_completion_metadata['transport_events'][0]['status_code']==401

def test_rate_limit_honors_bounded_retry_after_and_recovers():
    c=client()
    error=urllib.error.HTTPError('https://example.com',429,'limit',{'Retry-After':'999'},io.BytesIO(b'wait'))
    with patch('urllib.request.urlopen',side_effect=[error,Response()]),patch('time.sleep') as sleep:
        assert c.complete('prompt',16)=='<final>ok</final>'
    sleep.assert_called_once_with(10)

def test_new_logical_request_does_not_reuse_previous_usage_or_failures():
    c=client()
    with patch('urllib.request.urlopen',side_effect=[TimeoutError('timeout'),Response(),Response()]),patch('time.sleep'):
        c.complete('one',16)
        c.complete('two',16)
    assert c.last_completion_metadata['transport_attempts']==1
    assert c.last_completion_metadata['transport_failures']==0
    assert c.last_completion_metadata['transport_usage_complete']

def test_elapsed_retry_budget_prevents_a_second_full_timeout():
    c=client();clock=[0.0];timeouts=[]
    def request(*args,**kwargs):
        timeouts.append(kwargs['timeout'])
        clock[0]+=kwargs['timeout']
        raise TimeoutError('read timed out')
    def sleep(seconds):clock[0]+=seconds
    with patch('time.monotonic',side_effect=lambda:clock[0]),patch('urllib.request.urlopen',side_effect=request),patch('time.sleep',side_effect=sleep):
        with pytest.raises(TimeoutError):c.complete('prompt',16)
    assert timeouts==[30,29.5]
    assert c.last_completion_metadata['transport_attempts']==2

def test_runtime_records_each_transport_attempt_and_restores_existing_observer(tmp_path):
    from pico import Pico,SessionStore,WorkspaceContext
    c=client();external=[];observer=external.append;c.on_transport_event=observer
    a=Pico(c,WorkspaceContext.build(tmp_path,repo_root_override=tmp_path),SessionStore(tmp_path/'.pico/sessions'))
    with patch('urllib.request.urlopen',side_effect=[TimeoutError('timeout'),Response()]),patch('time.sleep'):
        assert a.ask('Reply')=='ok'
    trace=[json.loads(l) for l in a.run_store.trace_path(a.current_task_state).read_text(encoding='utf-8').splitlines()]
    transport=[e for e in trace if e['event']=='model_transport']
    assert len(transport)==2 and transport[0]['ok'] is False and transport[1]['ok'] is True
    assert c.on_transport_event is observer and len(external)==2
