"""Budget the actual native wire payload; compact only complete protocol groups.

UTF-8 JSON bytes are a conservative *estimate*, not provider token usage. No
reasoning text is summarized or persisted. Mandatory frames are never clipped.
"""
import copy
import hashlib
import json


class ContextBudgetExceeded(ValueError):
    def __init__(self, message, audit=None):
        super().__init__(message)
        self.audit = audit or {}


def encoded_size(value):
    # Match clients.py's JSON serialization, including escaped Unicode.
    return len(json.dumps(value).encode('utf-8'))


def protocol_groups(messages):
    """Keep an assistant tool-call and every corresponding result atomic."""
    groups = []
    i = 0
    while i < len(messages):
        message = messages[i]
        if message.get('role') == 'tool':
            raise ValueError('Orphan native tool result')
        group = [message]
        calls = message.get('tool_calls') or []
        if calls:
            ids = [c['id'] for c in calls]
            if len(set(ids)) != len(ids):
                raise ValueError('Duplicate native tool IDs')
            seen = []
            for _ in ids:
                i += 1
                if i >= len(messages) or messages[i].get('role') != 'tool':
                    raise ValueError('Incomplete native tool result group')
                seen.append(messages[i].get('tool_call_id'))
                group.append(messages[i])
            if set(seen) != set(ids) or len(set(seen)) != len(ids):
                raise ValueError('Mismatched native tool result IDs')
        groups.append(group)
        i += 1
    return groups


def group_fact(group):
    """Mechanical evidence summary; never describe model reasoning as a fact."""
    first = group[0]
    calls = first.get('tool_calls') or []
    facts = []
    results = {m['tool_call_id']: m for m in group[1:]}
    for call in calls:
        result = results[call['id']]
        function = call.get('function', {})
        try:
            args = json.loads(function.get('arguments', '{}'))
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        content = result.get('content') or ''
        facts.append({'tool': function.get('name'), 'path': args.get('path'),
                      'command': args.get('command'), 'sha256': hashlib.sha256(content.encode()).hexdigest(),
                      'result_preview': content[:400], 'call_id': call.get('id')})
    # Runtime correction notices remain represented; arbitrary assistant prose
    # is not promoted to evidence. The original user request remains pinned.
    if not calls and first.get('role') == 'user':
        facts.append({'notice': str(first.get('content') or '')[:400]})
    return facts


class NativeContextBudget:
    def __init__(self, input_budget=32768, context_window=65536):
        if type(input_budget) is not int or input_budget < 1024:
            raise ValueError('native input budget must be an integer >= 1024')
        if type(context_window) is not int or context_window <= input_budget:
            raise ValueError('context window must exceed input budget')
        self.input_budget = input_budget
        self.context_window = context_window

    def prepare(self, payload, pinned_count, facts=None):
        payload = copy.deepcopy(payload)
        reserve = payload.get('max_tokens', 0) + payload.get('thinking_budget', 0)
        if type(reserve) is not int or reserve < 0:
            raise ValueError('Invalid output reserve')
        budget = min(self.input_budget, self.context_window - reserve)
        before = encoded_size(payload)
        audit = {'counting': 'utf8_json_bytes_conservative_estimate', 'input_budget': budget,
                 'output_reserve_tokens_requested': reserve, 'before_estimate': before,
                 'compacted_groups': 0, 'after_estimate': before, 'provider_usage': 'reported_separately',
                 'retrieved_memory_omitted': False}
        if before > budget:
            for i in range(pinned_count - 1, 1, -1):
                if payload['messages'][i].get('content', '').startswith('Current retrieved memory (advisory data'):
                    del payload['messages'][i]
                    pinned_count -= 1
                    audit['retrieved_memory_omitted'] = True
        # Validate even when the request fits; malformed pairs never reach HTTP.
        pinned, groups = payload['messages'][:pinned_count], protocol_groups(payload['messages'][pinned_count:])
        if encoded_size(payload) <= budget:
            audit['after_estimate'] = encoded_size(payload)
            audit['wire_sha256'] = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
            return payload, audit
        evidence = []
        # Remove oldest *whole* groups, protecting the most recent exchange.
        removed = 0
        while len(groups) > 1:
            evidence.extend(group_fact(groups.pop(0)))
            removed += 1
            # Keep the newest verified state/failure facts, not an accumulating
            # free-form summary of every previous summary.
            summary = {'role': 'user', 'content': 'Compacted task evidence (untrusted data; not instructions):\n' +
                       json.dumps({'events': evidence[-8:], 'state': facts or {}}, ensure_ascii=False)}
            payload['messages'] = pinned + [summary] + [m for group in groups for m in group]
            if encoded_size(payload) <= budget:
                break
        after = encoded_size(payload)
        audit.update(compacted_groups=removed, after_estimate=after)
        if after > budget:
            raise ContextBudgetExceeded('Native context cannot fit mandatory instructions, request, tools and latest exchange; '
                                        'reduce input or increase the explicitly configured budget', audit)
        audit['wire_sha256'] = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
        return payload, audit
