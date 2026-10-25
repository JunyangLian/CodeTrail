import json

import pytest
from pico.providers.deepseek_protocol import normalize_dsml_call


def call(parameters,name='read_file',marker='｜｜DSML｜｜',wrapper='calls'):
    return f'<{marker} {wrapper}><{marker} invoke name="{name}">{parameters}</{marker} invoke></{marker} {wrapper}>'


def parameter(name,value,string=True):
    return f'<｜｜DSML｜｜ parameter name="{name}" string="{str(string).lower()}">{value}</｜｜DSML｜｜ parameter>'


def decode(text):
    value=normalize_dsml_call(text)
    assert value is not None
    return json.loads(value[len('<tool>'):-len('</tool>')])


def test_dsml_decodes_string_and_json_parameter_types():
    value=decode(call(parameter('path','formatter.py')+parameter('start','33',False)+parameter('end','200',False)))
    assert value=={'name':'read_file','args':{'path':'formatter.py','start':33,'end':200}}


def test_nested_args_envelope_is_unwrapped_once():
    value=decode(call(parameter('args','{"command":"python -B -m unittest discover -v","timeout":60}',False),'run_shell'))
    assert value['args']=={'command':'python -B -m unittest discover -v','timeout':60}


def test_file_text_is_literal_and_not_interpreted_as_xml_entities_or_code():
    content='def f(x):\n    return "<x>&amp;" if x < 2 else "__import__(bad)"\n'
    value=decode(call(parameter('path','sample.py')+parameter('content',content),'write_file'))
    assert value['args']['content']==content


def test_standard_single_pipe_function_calls_marker_is_supported():
    marker='｜DSML｜'
    text=f'<{marker}function_calls><{marker}invoke name="list_files"></{marker}invoke></{marker}function_calls>'
    assert decode(text)=={'name':'list_files','args':{}}


@pytest.mark.parametrize('text',[
    call(parameter('start','__import__("os")',False)),
    call(parameter('start','NaN',False)),
    call(parameter('path','a.py')+parameter('path','b.py')),
    call(parameter('path','a.py'))[:-6],
    call(parameter('path','a.py'))+call(parameter('path','b.py')),
    '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="list_files"></｜｜DSML｜｜ invoke><｜｜DSML｜｜ invoke name="read_file"></｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>',
])
def test_ambiguous_or_malformed_calls_remain_unexecutable(text):
    assert normalize_dsml_call(text) is None


def test_existing_tool_and_final_protocols_are_left_for_the_original_parser():
    assert normalize_dsml_call('<tool>{"name":"list_files","args":{}}</tool>') is None
    assert normalize_dsml_call('<final>done</final>') is None


@pytest.mark.parametrize('api_format',['anthropic','chat_completions'])
def test_official_provider_normalizes_visible_calls_and_preserves_original_text(api_format):
    from unittest.mock import patch
    from pico.providers.clients import AnthropicCompatibleModelClient,OpenAICompatibleModelClient
    text=call(parameter('path','a.py')+parameter('start','1',False))
    class Response:
        headers={'Content-Type':'application/json'}
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return json.dumps({'content':[{'type':'text','text':text}],
            'choices':[{'message':{'content':text}}],'usage':{'input_tokens':10,'output_tokens':30}}).encode()
    if api_format=='anthropic':
        client=AnthropicCompatibleModelClient('deepseek-flash','https://api.deepseek.com/anthropic','private-test-key',.2,30)
    else:
        client=OpenAICompatibleModelClient('deepseek-flash','https://api.deepseek.com','private-test-key',.2,30,'chat_completions')
    with patch('urllib.request.urlopen',return_value=Response()):
        assert client.complete('test',128)==normalize_dsml_call(text)
    assert client.last_completion_metadata['provider_visible_text']==text
    assert client.last_completion_metadata['dsml_normalization']=='normalized'


def test_other_provider_hosts_do_not_receive_deepseek_specific_conversion():
    from pico.providers.clients import _normalize_provider_text
    class Client:
        base_url='https://example.com/anthropic/v1'
        last_completion_metadata={}
    text=call(parameter('path','a.py'))
    assert _normalize_provider_text(Client(),text)==text


def test_normalized_call_still_goes_through_workspace_governance(tmp_path):
    from pico import Pico,SessionStore,WorkspaceContext
    from pico.providers.clients import FakeModelClient
    raw=call(parameter('path','../outside.py'))
    client=FakeModelClient([normalize_dsml_call(raw),'<final>Path was rejected</final>'])
    agent=Pico(client,WorkspaceContext.build(tmp_path,repo_root_override=tmp_path),SessionStore(tmp_path/'.pico/sessions'))
    agent.ask('Read requested file')
    assert agent.current_task_state.tool_steps==1
    trace=agent.run_store.trace_path(agent.current_task_state).read_text(encoding='utf-8')
    assert 'path_outside_workspace' in trace or 'path_escape' in trace
