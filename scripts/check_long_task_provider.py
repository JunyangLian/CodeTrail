"""Optional two-session read-only provider smoke; no repair benchmark score.

Uses SILICONFLOW_API_KEY from the process environment only. Freezes the plan
before requests, keeps failures, stops on the first API error, and never writes
implementation files or retries a completed smoke to improve its result.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pico import Pico, SessionStore, WorkspaceContext
from pico.evaluation.repair_suite import install, inventory
from pico.security import redact_artifact
from scripts.run_final_agent_eval import client, CONFIG


def save(path, value):
    path.write_text(json.dumps(redact_artifact(value), ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    assets = ROOT / 'benchmarks/final_agent_v1/tasks.json'
    task = next(t for t in json.loads(assets.read_text(encoding='utf-8'))['tasks'] if t['id'] == 'pagination_boundaries')
    prompts = [
        '只读取 pagination.py，列出 paginate 的准确函数签名和参数名。不修复任何缺陷，不运行命令、不修改文件。',
        '请从已验证的仓库结构记忆中报告 pagination.py 的 paginate 函数签名和参数名。记忆不足时可读取源码；不要运行命令或修改文件。',
    ]
    save(output / 'protocol.json', {'kind': 'read-only two-session provider compatibility smoke, not repair evaluation',
        'model': CONFIG['model'], 'provider': 'siliconflow', 'base_url': CONFIG['base_url'],
        'temperature': CONFIG['temperature'], 'top_p': CONFIG['top_p'], 'max_tokens': 4096,
        'thinking_budget': 4096, 'timeout': 180, 'transport_attempts': 1,
        'max_steps': 4, 'native_input_budget': 32768, 'native_context_window': 65536,
        'prompts': prompts, 'task_asset_sha256': hashlib.sha256(assets.read_bytes()).hexdigest(),
        'source_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / 'pico').rglob('*.py')}})
    if not os.environ.get('SILICONFLOW_API_KEY'):
        save(output / 'failure.json', {'stage': 'preflight', 'error': 'SILICONFLOW_API_KEY is not configured'})
        return 1
    urllib.request.urlopen = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
    workspace = output / 'workspace'
    install(workspace, task['files'])
    before = inventory(workspace)
    rows = []
    try:
        for index, prompt in enumerate(prompts, 1):
            model = client()
            model.max_transport_attempts = 1
            wire = []
            def observe(event):
                body = json.loads(event['body'])
                # Provider reasoning is kept out of exported prose; preserve its hash.
                def mask(value):
                    if isinstance(value, dict):
                        result = {}
                        for key, item in value.items():
                            if key == 'reasoning_content':
                                result['reasoning_audit'] = {'chars': len(item or ''), 'sha256': hashlib.sha256((item or '').encode()).hexdigest()}
                            else:
                                result[key] = mask(item)
                        return result
                    if isinstance(value, list):
                        return [mask(item) for item in value]
                    return value
                wire.append({'event': event['event'], 'original_sha256': event.get('sha256'), 'body': mask(body)})
                save(output / f'wire-{index}.json', wire)
            model.on_wire_event = observe
            agent = Pico(model, WorkspaceContext.build(workspace, repo_root_override=workspace),
                SessionStore(workspace / '.pico/sessions'), approval_policy='never', read_only=True,
                allowed_tools=['read_file', 'search', 'list_files'], max_steps=4, max_depth=0, max_new_tokens=4096)
            answer = agent.ask(prompt)
            report = agent.run_store.load_report(agent.current_task_state.run_id)
            requests = [e['body'] for e in wire if e['event'] == 'request']
            memory_on_wire = any(any('Current retrieved memory' in m.get('content', '') and 'verified_source_or_operator' in m['content']
                                    for m in request.get('messages', [])) for request in requests)
            row = {'session': index, 'session_id': agent.session['id'], 'run_id': agent.current_task_state.run_id,
                   'status': agent.current_task_state.status, 'tool_steps': agent.current_task_state.tool_steps,
                   'answer': answer, 'memory_on_wire': memory_on_wire,
                   'provider_model': report.get('prompt_metadata', {}).get('provider_model'),
                   'source_files_unchanged': inventory(workspace) == before,
                   'signature_name_check': all(name in answer for name in ('paginate', 'items', 'page', 'page_size'))}
            rows.append(row)
            save(output / 'results.json', rows)
            print(json.dumps({k: row[k] for k in ('session', 'status', 'tool_steps', 'memory_on_wire', 'source_files_unchanged', 'signature_name_check')}, ensure_ascii=False), flush=True)
            if row['status'] != 'completed':
                raise RuntimeError('Smoke did not complete; no replacement run')
    except Exception as exc:
        save(output / 'failure.json', {'stage': 'provider_or_runtime', 'error_type': type(exc).__name__, 'error': str(exc), 'completed_sessions': len(rows)})
        print('Smoke stopped; failure and trace retained.', flush=True)
        return 1
    return 0 if all(r['source_files_unchanged'] and r['signature_name_check'] for r in rows) and rows[1]['memory_on_wire'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
