"""Convert one complete visible DeepSeek DSML call to the Harness tool envelope.

This only decodes data. Normal argument validation, workspace limits and approval
remain the responsibility of the existing tool executor.
"""
import json
import re


def normalize_dsml_call(text):
    """Return a canonical call, or None for non-DSML/ambiguous/truncated input."""
    wrapper=re.fullmatch(
        r'\s*<(?P<marker>｜{1,2}DSML｜{1,2})\s*(?P<kind>function_calls|calls)>\s*'
        r'(?P<body>.*?)\s*</(?P=marker)\s*(?P=kind)>\s*',text,re.S)
    if not wrapper:
        return None
    marker=re.escape(wrapper['marker'])
    invocation=re.fullmatch(r'<'+marker+r'\s*invoke\s+name="(?P<name>[A-Za-z_][A-Za-z_0-9]*)">'
        r'(?P<body>.*?)</'+marker+r'\s*invoke>',wrapper['body'].strip(),re.S)
    if not invocation:
        return None
    parameter=re.compile(r'<'+marker+r'\s*parameter\s+name="(?P<name>[A-Za-z_][A-Za-z_0-9]*)"'
        r'\s+string="(?P<string>true|false)">(?P<value>.*?)</'+marker+r'\s*parameter>',re.S)
    arguments={};body=invocation['body'];position=0
    for match in parameter.finditer(body):
        if body[position:match.start()].strip() or match['name'] in arguments:
            return None
        value=match['value']
        # Reserved delimiters embedded in an argument make framing ambiguous.
        if re.search(r'</?｜{1,2}DSML｜{1,2}',value):
            return None
        if match['string']=='false':
            try:
                def invalid_constant(value):raise ValueError('Non-JSON constant')
                value=json.loads(value,parse_constant=invalid_constant)
            except (ValueError,TypeError):
                return None
        arguments[match['name']]=value;position=match.end()
    if body[position:].strip():
        return None
    if set(arguments)=={'args'} and isinstance(arguments['args'],dict):
        arguments=arguments['args']
    return '<tool>'+json.dumps({'name':invocation['name'],'args':arguments},ensure_ascii=False)+'</tool>'
