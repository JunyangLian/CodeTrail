import json
from unittest.mock import patch

import pytest

from pico.providers.clients import OpenAICompatibleModelClient
from pico.providers.native_tools import NativeToolConversation
from pico.tools import BASE_TOOL_SPECS


def reply(calls=None, content=None, reasoning='private internal reasoning', finish=None):
    message = {'content': content, 'reasoning_content': reasoning}
    if calls is not None:
        message['tool_calls'] = calls
    return {'choices': [{'message': message, 'finish_reason': finish or ('tool_calls' if calls else 'stop')}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}


def call(name='read_file', args=None, id='actual-id'):
    return {'id': id, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(args if args is not None else {'path': 'a.py'})}}


def conversation():
    return NativeToolConversation(BASE_TOOL_SPECS, 'workspace', 'repair', 'python -m unittest')


def test_actual_ids_and_thinking_replayed_but_not_audited():
    state = conversation(); metadata = {}
    action = state.accept(reply([call()]), metadata)
    assert action.action == {'name': 'read_file', 'args': {'path': 'a.py'}}
    with pytest.raises(RuntimeError):
        state.payload()
    state.observe('untruncated tool result')
    wire = state.payload()
    assert wire['messages'][-2]['reasoning_content'] == 'private internal reasoning'
    assert wire['messages'][-1] == {'role': 'tool', 'tool_call_id': 'actual-id', 'content': 'untruncated tool result'}
    assert 'private internal reasoning' not in json.dumps(state.audit_request())
    assert 'private internal reasoning' not in json.dumps(metadata)
    state.accept(reply(content='Finished'), metadata)
    assert state.payload()['messages'][-1]['reasoning_content'] == 'private internal reasoning'


@pytest.mark.parametrize('calls', [[call(id='a'), call(id='b')], [call(args=[])],
    [call(args={'path': 'a.py', 'new_text': '</｜｜DSML｜｜ parameter>'})]])
def test_protocol_errors_close_all_actual_ids_without_executing(calls):
    state = conversation(); metadata = {}
    value = state.accept(reply(calls), metadata)
    assert value.action_kind == 'retry' and metadata['protocol_error']
    assert [m['tool_call_id'] for m in state.payload()['messages'] if m['role'] == 'tool'] == [c['id'] for c in calls]


def test_missing_reasoning_and_truncation_do_not_poison_next_request():
    state = conversation(); original = state.payload()['messages']
    for value in (reply([call()], reasoning=None), reply([call()], finish='length')):
        assert state.accept(value, {}).action_kind == 'retry'
        assert state.payload()['messages'] == original


def test_literal_tool_closing_tag_in_native_patch_is_not_parsed_as_transport():
    state = conversation()
    value = state.accept(reply([call('patch_file', {'path': 'a', 'old_text': 'x', 'new_text': '</tool>'})]), {})
    assert value.action['args']['new_text'] == '</tool>'


def test_native_provider_wire_and_retry_preserve_history():
    client = OpenAICompatibleModelClient('deepseek-flash', 'https://api.deepseek.com/v1', 'fake-test-key', .2, 30, 'chat_completions')
    client.begin_native_run(BASE_TOOL_SPECS, 'workspace', 'repair', None)
    sent = []
    class Response:
        headers = {'Content-Type': 'application/json'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(reply([call()] if len(sent) == 1 else None, content='done')).encode()
    def urlopen(request, **kwargs):
        sent.append(json.loads(request.data)); return Response()
    with patch('urllib.request.urlopen', side_effect=urlopen):
        first = client.complete('legacy XML prompt must not be sent', 8192)
        client.observe_native_tool('result')
        second = client.complete('another ignored flattened prompt', 8192)
    assert first.action_kind == 'tool' and second.action_kind == 'final'
    assert sent[0]['thinking'] == {'type': 'enabled'} and sent[0]['tool_choice'] == 'auto'
    assert sent[1]['messages'][-2]['reasoning_content'] == 'private internal reasoning'
    assert sent[1]['messages'][-1]['tool_call_id'] == 'actual-id'
    assert 'legacy XML prompt' not in json.dumps(sent)
    client.begin_native_run(BASE_TOOL_SPECS, 'another workspace', 'new task', None)
    assert len(client.native_conversation.messages) == 2


def test_agent_patch_failure_autocorrection_and_same_safety_boundary(tmp_path):
    from pico.runtime import Pico, SessionStore
    from pico.workspace import WorkspaceContext
    (tmp_path / 'a.py').write_text('value = 1\n')
    client = OpenAICompatibleModelClient('deepseek-flash', 'https://api.deepseek.com', 'fake', .2, 30, 'chat_completions')
    outputs = [reply([call('patch_file', {'path': 'a.py', 'old_text': 'wrong', 'new_text': 'value = 2'})]),
               reply([call('read_file', {'path': 'a.py'}, 'read-id')]),
               reply([call('patch_file', {'path': 'a.py', 'old_text': 'value = 1', 'new_text': 'value = 2'}, 'fix-id')]),
               reply([call('read_file', {'path': '../outside'}, 'escape-id')]), reply(content='Fixed')]
    class Response:
        headers = {'Content-Type': 'application/json'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(outputs.pop(0)).encode()
    agent = Pico(client, WorkspaceContext.build(tmp_path, repo_root_override=tmp_path),
                 SessionStore(tmp_path / '.pico/sessions'), max_steps=6, max_depth=0)
    approvals = []
    agent.approve = lambda name, args: approvals.append(name) or True
    with patch('urllib.request.urlopen', return_value=Response()):
        agent.ask('fix a.py')
    assert (tmp_path / 'a.py').read_text() == 'value = 2\n'
    assert approvals == ['patch_file']
    messages = client.native_conversation.messages
    assert any(m['role'] == 'tool' and 'No edit was applied' in m['content'] for m in messages)
    assert any(m['role'] == 'tool' and 'path escapes workspace' in m['content'] for m in messages)
    saved = ''.join(p.read_text('utf-8') for p in (tmp_path / '.pico').rglob('*.json*'))
    assert 'private internal reasoning' not in saved


def test_read_pagination_exposes_missing_range_and_observes_new_content(tmp_path):
    from pico.runtime import Pico, SessionStore
    from pico.workspace import WorkspaceContext
    from pico.providers.clients import FakeModelClient
    (tmp_path / 'long.py').write_text('\n'.join('line' + str(i) + ' ' * 100 for i in range(100)))
    agent = Pico(FakeModelClient([]), WorkspaceContext.build(tmp_path, repo_root_override=tmp_path), SessionStore(tmp_path / '.pico/sessions'))
    result = agent.execute_tool('read_file', {'path': 'long.py', 'start': 1, 'end': 100}).content
    assert '[Unread lines ' in result and len(result) < 4000
    (tmp_path / 'long.py').write_text('changed')
    assert 'changed' in agent.execute_tool('read_file', {'path': 'long.py'}).content


def test_mixed_dsml_and_multiple_text_calls_never_execute():
    from pico.runtime import Pico
    assert Pico.parse('<tool>{"name":"write_file","args":{"new_text":"</｜｜DSML｜｜ parameter>"}}</tool>')[0] == 'retry'
    assert Pico.parse('<tool>{"name":"list_files"}</tool><tool>{"name":"read_file"}</tool>')[0] == 'retry'


def test_recorded_native_run_rejects_premature_final_and_does_not_persist_reasoning(tmp_path):
    from pico.runtime import Pico, SessionStore
    from pico.workspace import WorkspaceContext
    from pico.evaluation.repair_suite import RecordingClient
    from pico.tool_executor import ToolExecutionResult
    client = OpenAICompatibleModelClient('deepseek-flash', 'https://api.deepseek.com', 'fake', .2, 30, 'chat_completions')
    recorder = RecordingClient(client, tmp_path / 'recording.jsonl')
    outputs = [reply(content='Done without testing'),
               reply([call('run_shell', {'command': 'python -m unittest'}, 'verify-id')]), reply(content='Verified')]
    class Response:
        headers = {'Content-Type': 'application/json'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(outputs.pop(0)).encode()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    agent = Pico(recorder, WorkspaceContext.build(workspace, repo_root_override=workspace), SessionStore(workspace / '.pico/sessions'),
                 max_steps=3, max_depth=0, verification_command='python -m unittest')
    agent.execute_tool = lambda *args: ToolExecutionResult('exit_code: 0', {'exit_code': 0, 'tool_status': 'ok'})
    with patch('urllib.request.urlopen', return_value=Response()):
        assert agent.ask('verify') == 'Verified'
    assert agent.last_verification['status'] == 'passed'
    data = (tmp_path / 'recording.jsonl').read_text('utf-8')
    assert 'private internal reasoning' not in data
    requests = [json.loads(l) for l in data.splitlines() if json.loads(l)['event'] == 'request']
    assert requests[-1]['native_request']['messages'][-1]['tool_call_id'] == 'verify-id'
    assert requests[-1]['native_request']['messages'][-2]['reasoning_audit']['chars'] > 0


def test_native_text_protocol_is_rejected_and_other_hosts_keep_legacy_protocol():
    state = conversation()
    assert state.accept(reply(content='<tool>{"name":"list_files"}</tool>'), {}).action_kind == 'retry'
    client = OpenAICompatibleModelClient('model', 'https://example.com/v1', 'fake', .2, 30, 'chat_completions')
    assert not client.supports_native_tools


def test_read_only_delegate_does_not_overwrite_parent_tool_ids_or_reasoning(tmp_path):
    from pico.runtime import Pico, SessionStore
    from pico.workspace import WorkspaceContext
    client = OpenAICompatibleModelClient('deepseek-flash', 'https://api.deepseek.com', 'fake', .2, 30, 'chat_completions')
    outputs = [reply([call('delegate', {'task': 'inspect'}, 'parent-id')]), reply(content='Child inspected'), reply(content='Parent done')]
    class Response:
        headers = {'Content-Type': 'application/json'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(outputs.pop(0)).encode()
    agent = Pico(client, WorkspaceContext.build(tmp_path, repo_root_override=tmp_path), SessionStore(tmp_path / '.pico/sessions'), max_steps=2)
    with patch('urllib.request.urlopen', return_value=Response()):
        assert agent.ask('investigate') == 'Parent done'
    messages = client.native_conversation.messages
    assert messages[-2] == {'role': 'tool', 'tool_call_id': 'parent-id', 'content': 'delegate_result:\nChild inspected'}
    assert all(m.get('content') != 'Child inspected' for m in messages)
