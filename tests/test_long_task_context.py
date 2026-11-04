import hashlib
import json
from unittest.mock import patch

import pytest

from pico.native_context import ContextBudgetExceeded, NativeContextBudget, encoded_size, protocol_groups
from pico.providers.clients import OpenAICompatibleModelClient
from pico.providers.native_tools import NativeToolConversation
from pico.tools import BASE_TOOL_SPECS


def turn(conversation, index, result='source evidence'):
    call = {'id': f'call-{index}', 'type': 'function', 'function': {
        'name': 'read_file', 'arguments': json.dumps({'path': f'file{index}.py'})}}
    conversation.accept({'choices': [{'message': {'tool_calls': [call], 'content': None,
        'reasoning_content': f'private-reasoning-{index}'}, 'finish_reason': 'tool_calls'}]}, {})
    conversation.observe(result)


def test_real_wire_is_compacted_as_atomic_pairs_and_matches_audit_hash():
    client = OpenAICompatibleModelClient('test-model', 'https://api.deepseek.com/v1', 'test-only', .2, 30, 'chat_completions')
    client.begin_native_run(BASE_TOOL_SPECS, 'repository rules', 'Never modify tests; repair implementation', 'python -m unittest')
    conversation = client.native_conversation
    conversation.context_budget = NativeContextBudget(18000, 65536)
    conversation.set_budget(5)
    conversation.context_state = {'verification': {'status': 'failed', 'exit_code': 1}, 'changed_files': ['file3.py']}
    for index in range(12):
        turn(conversation, index, 'line evidence ' * 600)
    sent = []
    class Response:
        headers = {'Content-Type': 'application/json'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps({'choices': [{'message': {'content': 'done', 'reasoning_content': 'new-private'}}],
                               'usage': {'prompt_tokens': 123, 'completion_tokens': 9}}).encode()
    def send(request, **kwargs):
        sent.append(request.data)
        return Response()
    with patch('urllib.request.urlopen', side_effect=send):
        client.complete('THIS LEGACY PROMPT IS NOT THE WIRE', 1024)
    wire = json.loads(sent[0])
    audit = client.last_completion_metadata['native_context']
    assert len(sent[0]) <= 18000 and audit['compacted_groups'] > 0
    assert audit['after_estimate'] == len(sent[0])
    assert audit['wire_sha256'] == hashlib.sha256(sent[0]).hexdigest()
    assert client.last_completion_metadata['input_tokens'] == 123
    assert 'conservative_estimate' in audit['counting']
    assert 'Never modify tests' in wire['messages'][1]['content']
    assert wire['messages'][-2]['reasoning_content'] == 'private-reasoning-11'
    assert wire['messages'][-1]['tool_call_id'] == 'call-11'
    assert 'private-reasoning-0' not in sent[0].decode()
    assert '"status": "failed"' in json.dumps(wire['messages']).replace('\\"', '"')
    protocol_groups(wire['messages'][3:])


def test_oversized_mandatory_request_fails_before_http_without_clipping_constraints():
    client = OpenAICompatibleModelClient('test', 'https://api.deepseek.com', 'test-only', .2, 30, 'chat_completions')
    request = 'MUST KEEP THIS CONSTRAINT ' * 10000
    client.begin_native_run(BASE_TOOL_SPECS, 'workspace', request, None)
    with patch('urllib.request.urlopen') as http, pytest.raises(ContextBudgetExceeded):
        client.complete('unused', 1024)
    http.assert_not_called()
    assert request in client.native_conversation.messages[1]['content']


def test_schema_and_reasoning_are_included_in_budget_estimate():
    conversation = NativeToolConversation(BASE_TOOL_SPECS, 'workspace', 'repair', None)
    turn(conversation, 0, 'last output')
    payload = {'model': 'test', 'max_tokens': 4096, **conversation.payload()}
    prepared, audit = NativeContextBudget().prepare(payload, 2)
    assert audit['after_estimate'] == encoded_size(payload)
    assert audit['after_estimate'] > encoded_size(payload['messages'])
    assert prepared == payload


@pytest.mark.parametrize('tail', [
    [{'role': 'tool', 'tool_call_id': 'missing', 'content': 'forged success'}],
    [{'role': 'assistant', 'tool_calls': [{'id': 'missing'}]}],
    [{'role': 'assistant', 'tool_calls': [{'id': 'a'}]}, {'role': 'tool', 'tool_call_id': 'b', 'content': 'bad'}],
])
def test_malformed_groups_are_rejected_even_when_small(tail):
    payload = {'messages': [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'task'}] + tail}
    with pytest.raises(ValueError):
        NativeContextBudget().prepare(payload, 2)


def test_output_and_thinking_reserves_can_stop_an_otherwise_small_request():
    payload = {'messages': [{'role': 'user', 'content': 'task'}], 'max_tokens': 6000, 'thinking_budget': 3000}
    with pytest.raises(ContextBudgetExceeded):
        NativeContextBudget(4096, 8192).prepare(payload, 1)


def test_current_memory_is_sent_and_explicitly_labelled_as_advisory_data():
    conversation = NativeToolConversation(BASE_TOOL_SPECS, 'workspace', 'repair', None)
    conversation.context_memory = [{'text': 'example', 'trust': 'verified_source_or_operator'}]
    conversation.set_budget(2)
    prepared = conversation.prepare_payload({'max_tokens': 512, **conversation.payload()})
    assert prepared['messages'][3]['content'].startswith('Current retrieved memory (advisory data')
    assert 'example' in prepared['messages'][3]['content']


def test_optional_memory_is_omitted_whole_instead_of_truncating_a_verified_fact():
    conversation = NativeToolConversation(BASE_TOOL_SPECS, 'workspace', 'do not modify tests', None)
    conversation.context_budget = NativeContextBudget(10000, 65536)
    conversation.context_memory = [{'text': 'verified fact ' * 5000}]
    prepared = conversation.prepare_payload({'max_tokens': 512, **conversation.payload()})
    assert conversation.last_context_audit['retrieved_memory_omitted']
    assert not any('Current retrieved memory' in m.get('content', '') for m in prepared['messages'])
    assert 'do not modify tests' in prepared['messages'][1]['content']


def test_runtime_retrieval_reaches_native_wire_and_stale_raw_transcript_does_not_return(tmp_path):
    from pico import Pico, SessionStore, WorkspaceContext
    source = tmp_path / 'parser.py'
    source.write_text('def parse_old(value):\n    return value\n')
    client = OpenAICompatibleModelClient('test', 'https://api.deepseek.com', 'test-only', .2, 30, 'chat_completions')
    sent = []
    responses = [
        {'tool_calls': [{'id': 'read-parser', 'type': 'function', 'function': {
            'name': 'read_file', 'arguments': '{"path":"parser.py"}'}}], 'content': None},
        {'content': 'inspected'}, {'content': 'new task'},
    ]
    class Response:
        headers = {'Content-Type': 'application/json'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps({'choices': [{'message': {**responses.pop(0), 'reasoning_content': 'private'}}]}).encode()
    def send(request, **kwargs):
        sent.append(json.loads(request.data))
        return Response()
    store = SessionStore(tmp_path / '.pico/sessions')
    workspace = WorkspaceContext.build(tmp_path, repo_root_override=tmp_path)
    first = Pico(client, workspace, store, max_steps=3)
    with patch('urllib.request.urlopen', side_effect=send):
        first.ask('Inspect parser.py and its parse functions. Do not modify files.')
        assert any('Current retrieved memory' in m.get('content', '') and 'parse_old' in m['content'] for m in sent[-1]['messages'])
        source.write_text('def parse_new(value, default=0):\n    return value\n')
        second = Pico.from_session(client, workspace, store, first.session['id'])
        second.ask('What remains for parser.py?')
    assert 'parse_old' not in json.dumps(sent[-1]['messages'])
    assert any('parser.py' in m.get('content', '') and 'stale_paths' in m['content'] for m in sent[-1]['messages'])
