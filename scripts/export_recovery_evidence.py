"""Export the frozen v2 regression and its public source after credential scanning."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_repair_regression_v2 import frozen, VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', default='artifacts/repair-regression-v2-2026-10-24')
    parser.add_argument('--audit', default='artifacts/repair-regression-v2-audit-2026-10-24')
    parser.add_argument('--destination', default='benchmarks/results/repair-regression-v2-2026-10-24/evidence.zip')
    args = parser.parse_args()
    batch, audit, destination = [(ROOT / x).resolve() for x in (args.batch, args.audit, args.destination)]
    if any(ROOT not in p.parents for p in (batch, audit, destination)):
        parser.error('All paths must be inside the project')
    freeze = frozen()
    assert json.loads((batch/'results.json').read_text(encoding='utf-8'))['completed']
    assert json.loads((audit/'audit.json').read_text(encoding='utf-8'))['summary']['runs'] == 12
    assert not destination.exists(), 'Cannot overwrite evidence'
    secrets = []
    env = ROOT/'.env'
    if env.exists():
        for line in env.read_text(encoding='utf-8').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, value = line.split('=', 1)
            value = value.strip().strip('\"\'')
            if any(word in key.upper() for word in ('KEY','TOKEN','SECRET','PASSWORD')) and len(value) >= 8:
                secrets.append(value.encode())
    git = r'C:\Program Files\Git\cmd\git.exe' if sys.platform == 'win32' else 'git'
    # The explicitly staged public tree is the source allowlist. Private artifacts
    # are added only from these two controlled, fixed-task evaluation directories.
    tracked = subprocess.check_output([git, 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    files = {name: ROOT/name for name in tracked if name and not name.endswith('.zip') and (ROOT/name).is_file()}
    for folder in (batch, audit, VERSION):
        for path in folder.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                files[path.relative_to(ROOT).as_posix()] = path
    for name, expected in freeze['hashes'].items():
        path = ROOT/name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
        files[name] = path
    hashes = {}
    for name, path in sorted(files.items()):
        assert not (path.name.startswith('.env') and path.name != '.env.example'), name
        data = path.read_bytes()
        assert not any(value in data for value in secrets), 'Credential found in ' + name
        assert not re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}', data), name
        hashes[name] = hashlib.sha256(data).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(files.items()):
            archive.write(path, name)
        archive.writestr('EVIDENCE_MANIFEST.json', json.dumps({'sha256': hashes,
            'credential_scan': 'passed', 'scope': '12 v2 runs; original v1 archive excluded, obtain separately'}, indent=2))
    sha = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.sha256').write_text(sha+'  '+destination.name+'\n', encoding='ascii')
    print(json.dumps({'archive': str(destination), 'sha256': sha, 'files': len(files),
                      'bytes': destination.stat().st_size, 'credential_scan': 'passed'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
