import json
from unittest.mock import patch
import urllib.request

import pytest
from pico.providers.clients import AnthropicCompatibleModelClient
from scripts.run_deepseek_validation import AuditedClient


class Response:
    headers={'x-request-id':'public-id'}
    status=200
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self):return json.dumps({'content':[{'type':'text','text':'<final>OK</final>'}],
        'stop_reason':'end_turn','usage':{'input_tokens':12,'output_tokens':4}}).encode()


def client(tmp_path,url='https://api.deepseek.com/anthropic'):
    return AuditedClient(AnthropicCompatibleModelClient('deepseek-flash',url,'private-test-key',.2,30,{'type':'disabled'}),tmp_path)


def test_audited_official_adapter_keeps_usage_and_observes_attempt_without_credentials(tmp_path):
    c=client(tmp_path);observed=[];c.on_transport_event=observed.append
    with patch('urllib.request.urlopen',return_value=Response()) as original:
        assert c.complete('test',128)=='<final>OK</final>'
        assert urllib.request.urlopen is original
    assert c.last_completion_metadata['input_tokens']==12
    assert c.last_completion_metadata['transport_attempts']==1
    assert observed[0]['request_id']=='public-id'
    assert 'private-test-key' not in (tmp_path/'transport.jsonl').read_text(encoding='utf-8')


def test_official_adapter_refuses_any_other_key_destination(tmp_path):
    c=client(tmp_path,'https://example.com/anthropic')
    with patch('urllib.request.urlopen') as request:
        with pytest.raises(AssertionError):c.complete('test',128)
        request.assert_not_called()


def test_read_timeout_marks_unknown_usage_and_restores_urlopen(tmp_path):
    c=client(tmp_path)
    response=Response()
    def fail():raise TimeoutError('read timeout')
    response.read=fail
    with patch('urllib.request.urlopen',return_value=response) as original:
        with pytest.raises(TimeoutError):c.complete('test',128)
        assert urllib.request.urlopen is original
    assert c.last_completion_metadata['transport_failures']==1
    assert not c.last_completion_metadata['transport_usage_complete']
