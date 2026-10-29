"""Recheck exported Tabbit E2E evidence with frozen tests, without model calls.

Usage: python -X utf8 scripts/audit_tabbit_e2e.py --evidence artifacts/tabbit-e2e-2026-10-25 --output artifacts/your-new-e2e-audit
Outputs are exclusive; this never overwrites browser runs or frozen task assets.
"""
import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pico.evaluation.repair_suite import inventory, verify_pristine


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.evidence = args.evidence.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    protocol = read(args.evidence / 'protocol.json')
    asset = ROOT / 'benchmarks/final_agent_v1/tasks.json'
    if hashlib.sha256(asset.read_bytes()).hexdigest() != protocol['tasks_asset_sha256']:
        raise ValueError('Frozen task asset hash mismatch')
    tasks = {t['id']: t for t in read(asset)['tasks']}
    original = {r['task_id']: r for r in read(args.evidence / 'independent-results.json')}
    rows = []
    for case in protocol['cases']:
        task = tasks[case['task_id']]
        workspace = args.evidence / 'cases' / task['id'] / 'workspace'
        current = inventory(workspace)
        changes = sorted(name for name in current if current[name] != case['sha256'].get(name))
        protected_ok = (set(current) == set(case['sha256']) and
                        all(name in task['editable_files'] for name in changes))
        overlay = {name: (workspace / name).read_bytes() for name in task['editable_files']}
        external = verify_pristine(task, overlay, args.output / task['id'] / 'pristine')
        # First run is the frozen repair; later UI recovery/denial probes stay separate.
        report_path = sorted((workspace / '.pico/runs').glob('*/report.json'))[0]
        report = read(report_path)
        events = [json.loads(line) for line in report_path.with_name('trace.jsonl').read_text(encoding='utf-8').splitlines()]
        tools = [e for e in events if e['event'] == 'tool_executed']
        shells = [e for e in tools if e['name'] == 'run_shell']
        parsed = [e for e in events if e['event'] == 'model_parsed']
        metadata = [e['completion_metadata'] for e in parsed]
        usage_complete = all(type(m.get(k)) is int for m in metadata for k in ('input_tokens', 'output_tokens'))
        audits = original[task['id']]['sandbox_audits']
        isolated = len(audits) == len(shells) == 2 and all(
            a['kernel_token_is_appcontainer'] and not a['host_acl_changed'] and not a['host_fallback']
            and not a['network_capabilities'] and a['capabilities'] == [] for a in audits)
        started, ended = (datetime.fromisoformat(events[i]['created_at']) for i in (0, -1))
        row = dict(task_id=task['id'], category=task['category'], run_id=report['run_id'],
                   functional_passed=external['accepted'] and protected_ok,
                   workflow_passed=bool(external['accepted'] and protected_ok and isolated and
                                        report['status'] == 'completed' and report['completion_verification']['status'] == 'passed'
                                        and [s.get('exit_code') for s in shells] == [1, 0]),
                   tests_run=external['tests_run'], tool_steps=report['tool_steps'], attempts=report['attempts'],
                   protocol_rejections=sum(bool(m.get('protocol_error')) for m in metadata),
                   input_tokens=sum(m['input_tokens'] for m in metadata) if usage_complete else None,
                   output_tokens=sum(m['output_tokens'] for m in metadata) if usage_complete else None,
                   usage_complete=usage_complete, elapsed_seconds=round((ended-started).total_seconds(), 3),
                   protected_files_unchanged=protected_ok, appcontainer_shells_verified=isolated,
                   changed_files=changes, external_verifier=external,
                   failure_category=None if external['accepted'] and protected_ok else 'external_verification_or_scope')
        rows.append(row)
        print(task['id'], row['functional_passed'], row['workflow_passed'], flush=True)
    (args.output / 'audit.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    fields = [k for k in rows[0] if k not in {'external_verifier', 'changed_files'}]
    with (args.output / 'runs.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: r[k] for k in fields} for r in rows)
    raise SystemExit(0 if all(r['functional_passed'] and r['workflow_passed'] for r in rows) else 1)


if __name__ == '__main__':
    main()
