"""Bounded Chat Completions SSE assembly; partial calls are never executable."""
import json


def read_chat_stream(response, cancellation=None, on_delta=None):
    content, reasoning, calls = [], [], {}
    finish, usage, model, fingerprint, done = None, None, None, None, False
    total = 0
    while True:
        if cancellation:
            cancellation.check()
        line = response.readline(131073)
        if not line:
            break
        total += len(line)
        if len(line) > 131072 or total > 8_000_000:
            raise ValueError('SSE response exceeds bounded size')
        if not line.startswith(b'data:'):
            continue
        text = line[5:].strip().decode('utf-8')
        if text == '[DONE]':
            done = True
            break
        event = json.loads(text)
        if event.get('error'):
            raise RuntimeError('Streaming provider error: ' + str(event['error']))
        model = event.get('model') or model
        fingerprint = event.get('system_fingerprint') or fingerprint
        if event.get('usage') is not None:
            usage = event['usage']
        choices = event.get('choices', [])
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get('delta') or {}
        if delta.get('content'):
            content.append(delta['content'])
            if on_delta:
                on_delta(delta['content'])
        if delta.get('reasoning_content') is not None:
            reasoning.append(delta['reasoning_content'])
        for call in delta.get('tool_calls') or []:
            index = call.get('index')
            if type(index) is not int or not 0 <= index < 8:
                raise ValueError('Invalid streamed tool call index')
            item = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
            if call.get('type') and call['type'] != 'function':
                raise ValueError('Unsupported streamed call type')
            if call.get('id'):
                item['id'] += call['id']
            function = call.get('function') or {}
            item['function']['name'] += function.get('name') or ''
            item['function']['arguments'] += function.get('arguments') or ''
        finish = choice.get('finish_reason') or finish
    if not done or finish is None:
        raise ValueError('Incomplete SSE response; no tool call accepted')
    message = {'role': 'assistant', 'content': ''.join(content) or None}
    if reasoning:
        message['reasoning_content'] = ''.join(reasoning)
    if calls:
        if sorted(calls) != list(range(len(calls))):
            raise ValueError('Noncontiguous streamed tool calls')
        message['tool_calls'] = [calls[i] for i in sorted(calls)]
    return {'choices': [{'message': message, 'finish_reason': finish}], 'usage': usage or {},
            'model': model, 'system_fingerprint': fingerprint}
