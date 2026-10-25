import json
from unittest.mock import patch
from pico.providers.clients import OpenAICompatibleModelClient


def test_siliconflow_nullable_reasoning_keeps_native_ids_and_does_not_fabricate_thoughts():
    client=OpenAICompatibleModelClient('deepseek-ai/DeepSeek-V4.1-Flash','https://api.siliconflow.cn/v1','fake',.2,30,'chat_completions')
    client.top_p=.95;client.begin_native_run({'read_file':{'schema':{'path':'str'},'description':'Read'}},'workspace','inspect',None)
    sent=[]
    class Response:
        headers={'Content-Type':'application/json','x-siliconcloud-trace-id':'sf-trace'}
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self):
            message={'role':'assistant','reasoning_content':None,'content':None,
                     'tool_calls':[{'id':'sf-real','type':'function','function':{'name':'read_file','arguments':'{"path":"a.py"}'}}]}
            return json.dumps({'model':client.model,'choices':[{'message':message,'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':12,'completion_tokens':4}}).encode()
    def open(request,**kwargs):sent.append(json.loads(request.data));return Response()
    with patch('urllib.request.urlopen',side_effect=open):
        answer=client.complete('ignored',4096)
        assert answer.action_kind=='tool'
        client.observe_native_tool('source')
        assert client.native_conversation.payload()['messages'][-2]['reasoning_content'] is None
        client.native_conversation.audit_request()
    assert sent[0]['enable_thinking'] is True and sent[0]['thinking_budget']==4096
    assert sent[0]['top_p']==.95 and 'thinking' not in sent[0]
    assert client.last_completion_metadata['transport_events'][0]['request_id']=='sf-trace'
    assert not client.last_completion_metadata['reasoning_present']


def test_other_siliconflow_models_are_not_silently_given_deepseek_protocol():
    client=OpenAICompatibleModelClient('zai-org/GLM-5.3-Flash','https://api.siliconflow.cn/v1','fake',.2,30,'chat_completions')
    assert not client.supports_native_tools
