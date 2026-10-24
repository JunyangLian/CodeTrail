"""Fetch pinned upstream evidence for the repair suite (never used by the agent)."""
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'tmp/upstream'

def fetch(url, destination):
    if destination.exists():
        return destination.read_bytes()
    request = urllib.request.Request(url, headers={'User-Agent': 'CodeTrail-evaluation-source-audit'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return data

def main():
    for module in ('timeutils', 'iterutils', 'strutils'):
        url = f'https://api.github.com/repos/mahmoud/boltons/commits?path=boltons/{module}.py&per_page=30'
        rows = json.loads(fetch(url, CACHE / f'boltons-{module}-commits.json'))
        print(module, json.dumps([{'sha': row['sha'], 'title': row['commit']['message'].splitlines()[0]} for row in rows[:18]]))
    for number in (318, 320, 328, 329):
        url = f'https://api.github.com/repos/python-humanize/humanize/pulls/{number}'
        row = json.loads(fetch(url, CACHE / f'humanize-pr-{number}.json'))
        print('humanize', json.dumps({key: row[key] for key in ('number', 'title', 'merged', 'merge_commit_sha')}))

def acquire_evidence():
    specs = [('boltons_isoparse', 'mahmoud/boltons', '1f37cce42f8d3146342ccbd45492d3b3d9c52698', 'boltons/timeutils.py'),
             ('boltons_xfrange', 'mahmoud/boltons', '3e9983db7b59578add73c01cd31fa7ecd8da2e04', 'boltons/iterutils.py'),
             ('boltons_multireplace', 'mahmoud/boltons', '435774ef8b10c1355bf77483a837945034011754', 'boltons/strutils.py'),
             ('boltons_splitlines', 'mahmoud/boltons', 'd12d86a4706aa4a367a91679b0ff9b6a572e5298', 'boltons/strutils.py')]
    for number, task_id in ((318, 'humanize_list'), (320, 'humanize_fractional'), (328, 'humanize_metric'), (329, 'humanize_size')):
        pr = json.loads((CACHE / f'humanize-pr-{number}.json').read_bytes())
        module = {318: 'lists', 329: 'filesize'}.get(number, 'number')
        specs.append((task_id, 'python-humanize/humanize', pr['merge_commit_sha'], f'src/humanize/{module}.py'))
    evidence = []
    for task_id, repo, sha, module in specs:
        directory = CACHE / task_id
        commit = json.loads(fetch(f'https://api.github.com/repos/{repo}/commits/{sha}', directory / 'commit.json'))
        parent = commit['parents'][0]['sha']
        for name, revision in (('buggy', parent), ('fixed', sha)):
            fetch(f'https://raw.githubusercontent.com/{repo}/{revision}/{module}', directory / f'{name}-{Path(module).name}')
        license_name = 'LICENSE' if repo.startswith('mahmoud') else 'LICENCE'
        fetch(f'https://raw.githubusercontent.com/{repo}/{sha}/{license_name}', directory / 'LICENSE.txt')
        item = {'id': task_id, 'repository': f'https://github.com/{repo}', 'fix_commit': sha,
                'buggy_commit': parent, 'module': module, 'commit_url': commit['html_url'],
                'title': commit['commit']['message'].splitlines()[0]}
        evidence.append(item)
        print(json.dumps(item))
    (CACHE / 'evidence.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')

if __name__ == '__main__':
    import sys
    acquire_evidence() if '--acquire' in sys.argv else main()
