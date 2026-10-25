import hashlib
from io import BytesIO
import json
from unittest.mock import patch
import urllib.error
import pytest
from pico.providers.clients import OpenAICompatibleModelClient


def client():
    return OpenAICompatibleModelClient('test-model','https://api.siliconflow.cn/v1','unit-private-key',.2,1,'chat_completions')


class Response:
    headers={'Content-Type':'application/json'}
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self):return json.dumps({'choices':[{'message':{'content':'ok'}}],'usage':{'prompt_tokens':1,'completion_tokens':1}}).encode()


def test_wire_capture_excludes_authorization_and_records_failed_http_body():
    model=client();events=[];model.on_wire_event=events.append
    failure=urllib.error.HTTPError('https://api.siliconflow.cn/v1/chat/completions',500,'server failed',{},BytesIO(b'{"error":"overload"}'))
    with patch('urllib.request.urlopen',side_effect=[failure,Response()]),patch('pico.providers.clients.time.sleep'):
        assert model.complete('public input',16)=='ok'
    assert [e['event'] for e in events]==['request','http_error','response']
    assert 'unit-private-key' not in json.dumps(events)
    for e in events:assert hashlib.sha256(e['body'].encode()).hexdigest()==e['sha256']


def test_body_timeout_has_observed_stage_and_unknown_usage():
    model=client();model.max_transport_attempts=1
    response=Response();response.read=lambda: (_ for _ in ()).throw(TimeoutError('body wait'))
    with patch('urllib.request.urlopen',return_value=response):
        with pytest.raises(TimeoutError):model.complete('public input',16)
    assert model.last_completion_metadata['transport_events'][0]['phase']=='response_body'
    assert model.last_completion_metadata['unknown_usage_attempts']==1
