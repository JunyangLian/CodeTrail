"""Mechanical evidence-pointer verification against public files only.

Used by the computed_verified condition for output validation with at most
one bounded correction round. Semantics mirror the frozen grader's locator
resolution (a JSON pointer resolves iff the locator is a top-level key of
that file; a table pointer resolves iff the locator is an existing 1-based
file line; a FASTA pointer resolves iff the locator occurs in the file) but
the implementation is self-contained: it reads only the case's public
artifacts and never imports the grader or oracle, so the correction feedback
cannot leak any private or expected information.

The verifier never judges whether a pointer *supports* its claim -- that is
Layer 2 (human/semantic review). It only checks locatability.
"""
from __future__ import annotations

import json


def _resolve(name, locator, files):
    """Mirror the grader's _locator_valid semantics on public bytes."""
    content = files.get(name)
    if content is None:
        return False, 'missing_file'
    if name.endswith('.json'):
        try:
            data = json.loads(content)
        except ValueError:
            return False, 'unparseable_json'
        if isinstance(data, dict) and locator in data:
            return True, 'json_field'
        return False, 'json_field'
    if locator.isdigit():
        lines = content.splitlines()
        if 1 <= int(locator) <= len(lines):
            return True, 'numbered_line'
        return False, 'numbered_line'
    if locator in content:
        return True, 'substring'
    return False, 'substring'


def verify_pointers(final_content, case_dir):
    """Check every evidence pointer of one final answer against public bytes.

    Returns a mechanical report; empty/unparseable finals are reported as
    not parseable and carry no unresolved pointers (output-contract failures
    are scored by the rubric, not corrected here).
    """
    from pathlib import Path
    case_dir = Path(case_dir)
    try:
        value = json.loads(final_content or '')
    except ValueError:
        return {'parseable': False, 'pointers': [], 'unresolved': []}
    if not isinstance(value, dict):
        return {'parseable': False, 'pointers': [], 'unresolved': []}
    files = {}
    for path in sorted((case_dir / 'artifacts').glob('*')):
        if path.is_file():
            files['artifacts/' + path.name] = path.read_text(encoding='utf-8',
                                                             errors='replace')
    report = {'parseable': True, 'pointers': [], 'unresolved': []}
    for item in value.get('evidence') or []:
        pointer = str(item.get('pointer', ''))
        name, _, locator = pointer.partition(':')
        resolved, kind = _resolve(name, locator, files)
        report['pointers'].append({'pointer': pointer, 'resolved': resolved,
                                   'locator_kind': kind})
        if not resolved:
            report['unresolved'].append(pointer)
    return report


def correction_feedback(unresolved):
    """Bounded-correction user message: mechanical facts only, no expected
    labels, no verdict hints, nothing from private files."""
    listed = '\n'.join('- ' + pointer for pointer in unresolved)
    return """Your submitted JSON object contains evidence pointers that do not \
resolve against the public artifact files:
""" + listed + """

Each pointer must have the exact form <public path>:<locator>, where locator \
is an existing top-level JSON key of that file or an existing line number of \
that file. Fields returned by the organelle_summary tool are tool \
measurements, not fields of the artifact files; point at the artifact file \
and an existing key instead. Re-check every pointer against the actual file \
bytes and resubmit exactly one corrected JSON object with the same schema."""
