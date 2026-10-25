"""Export only retained fixed-task evidence, checking actual credentials recursively."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.config import _parse_env_line
from pico.evaluation.repair_suite import digest,load_json

NAME='deepseek-controlled-2026-10-25'
DEST=ROOT/'benchmarks/results'/NAME


def main():
    report=load_json(DEST/'audit.json')
    assert report['full_tests']['failures']==report['full_tests']['errors']==0
    assert len(report['independent_audit']['rows'])==12
    secrets=set()
    for project in (ROOT,ROOT.parent/'pico-main'):
        for path in project.glob('.env*'):
            if path.name=='.env.example' or not path.is_file():continue
            for line in path.read_text(encoding='utf-8').splitlines():
                item=_parse_env_line(line)
                if item and any(w in item[0].upper() for w in ('KEY','TOKEN','SECRET','PASSWORD')) and len(item[1])>=8:
                    secrets.add(item[1].encode())
    assert secrets
    files={}
    folders=[ROOT/'artifacts'/NAME,ROOT/'artifacts/deepseek-controlled-audit-2026-10-25',
             ROOT/'artifacts/deepseek-controlled-native-2026-10-25',DEST]
    for folder in folders:
        for path in folder.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                assert path.name not in {'evidence.zip','evidence.sha256'},'Do not overwrite existing evidence'
                files[path.relative_to(ROOT).as_posix()]=path
    for path in (ROOT/'artifacts').glob('native-*.xml'):files[path.relative_to(ROOT).as_posix()]=path
    for name in ['README.md','tests/test_native_tools.py','tests/test_pico.py','scripts/run_deepseek_controlled.py',
                 'scripts/analyze_deepseek_traces.py','scripts/audit_deepseek_controlled.py',
                 'scripts/audit_deepseek_validation.py',
                 'scripts/export_deepseek_controlled.py','artifacts/deepseek-root-causes-before-2026-10-25.json']:
        files[name]=ROOT/name
    scanned=0
    def scan(name,data):
        nonlocal scanned
        scanned+=1
        assert not any(secret in data for secret in secrets),'Credential found: '+name
        assert not re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}',data),name
        if name.endswith('.zip'):
            with zipfile.ZipFile(BytesIO(data)) as archive:
                for entry in archive.namelist():
                    if not entry.endswith('/'):scan(name+'!'+entry,archive.read(entry))
    hashes={}
    for name,path in sorted(files.items()):
        assert not path.name.startswith('.env'),name
        data=path.read_bytes();scan(name,data);hashes[name]=hashlib.sha256(data).hexdigest()
    destination=DEST/'evidence.zip'
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,path in sorted(files.items()):archive.write(path,name)
        archive.writestr('EVIDENCE_MANIFEST.json',json.dumps({'sha256':hashes,'credential_scan':'passed',
            'recursively_scanned_entries':scanned,'scope':'12 native retest runs, independent audit, 36-run offline analysis. Old raw evidence remains in the earlier official DeepSeek archive.'},indent=2))
    sha=digest(destination)
    (DEST/'evidence.sha256').write_text(sha+'  evidence.zip\n',encoding='ascii')
    print(json.dumps({'files':len(files),'scanned_entries':scanned,'bytes':destination.stat().st_size,
                      'sha256':sha,'credential_scan':'passed'}))


if __name__=='__main__':main()
