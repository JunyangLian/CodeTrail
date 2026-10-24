"""Export exact benchmark evidence after scanning configured credential values."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import SUITE, check_freeze, digest, load_json

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',default='artifacts/repair-suite-v1-2026-10-24')
    parser.add_argument('--audit',default='artifacts/repair-suite-v1-audit-2026-10-24')
    parser.add_argument('--destination',default='artifacts/codetrail-repair-suite-v1-evidence.zip')
    parser.add_argument('--env-file',default=str(ROOT/'.env'))
    args=parser.parse_args()
    batch=(ROOT/args.batch).resolve();audit=(ROOT/args.audit).resolve();destination=(ROOT/args.destination).resolve()
    if any(ROOT not in p.parents for p in (batch,audit,destination)):
        parser.error('evidence paths must be inside project')
    check_freeze()
    assert load_json(batch/'results.json')['completed']
    assert load_json(audit/'audit.json')['summary']['runs']==60
    assert not destination.exists(),'Refusing to replace an existing evidence archive'
    credentials=[]
    env_path=Path(args.env_file).resolve()
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            if line.lstrip().startswith('#') or '=' not in line:continue
            key,value=line.split('=',1);value=value.strip().strip('\"\'')
            if any(marker in key.upper() for marker in ('KEY','TOKEN','SECRET','PASSWORD')) and len(value)>=8:
                credentials.append(value.encode())
    files={}
    for folder in (batch,audit,SUITE):
        for path in folder.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                if path.name.startswith('.env'):
                    raise ValueError('Private configuration in evidence')
                files[path.relative_to(ROOT).as_posix()]=path
    # Include the reviewable public source snapshot, so the bundle can also
    # reproduce the automated tests. Git ignores credentials and private state.
    git=r'C:\Program Files\Git\cmd\git.exe' if sys.platform=='win32' else 'git'
    public=subprocess.check_output([git,'ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode('utf-8').split('\0')
    for relative in public:
        if not relative or relative.endswith('.zip'):
            continue
        path=ROOT/relative
        if path.name.startswith('.env') and path.name!='.env.example':
            raise ValueError('Private configuration tracked in public snapshot')
        if path.is_file():
            files[relative]=path
    for relative in check_freeze()['hashes']:
        files[relative]=ROOT/relative
    for name in ('audit_repair_suite.py','prepare_suite_demo.py','replay_repair_demo.py','export_repair_evidence.py'):
        files['scripts/'+name]=ROOT/'scripts'/name
    files['docs/repair-suite-v1-protocol.md']=ROOT/'docs/repair-suite-v1-protocol.md'
    hashes={}
    for relative,path in sorted(files.items()):
        content=path.read_bytes()
        if any(secret in content for secret in credentials):
            raise ValueError('Configured credential found in '+relative)
        if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}',content):
            raise ValueError('Credential signature found in '+relative)
        hashes[relative]=hashlib.sha256(content).hexdigest()
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for relative,path in sorted(files.items()):archive.write(path,relative)
        archive.writestr('EVIDENCE_MANIFEST.json',json.dumps({'sha256':hashes,'credential_scan':'passed','files':len(hashes)},ensure_ascii=False,indent=2))
    print(json.dumps({'archive':str(destination),'bytes':destination.stat().st_size,'sha256':digest(destination),'files':len(files),'credential_scan':'passed'},ensure_ascii=False))

if __name__=='__main__':main()
