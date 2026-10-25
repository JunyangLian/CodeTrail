"""Publish explicit evidence allowlist, including credential recovery; exclude private gold."""
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.run_final_agent_eval import DEFAULT,check
from scripts.run_final_credential_recovery import OUTPUT,SAFETY
from scripts.report_final_agent_eval import PUBLIC,AUDIT
from pico.evaluation.repair_suite import digest,load_json,write_json,utcnow
from pico.config import _parse_env_line


def main():
    check(DEFAULT);check(OUTPUT)
    assert (PUBLIC/'credential-recovery-audit.json').exists()
    assert load_json(SAFETY/'results.json')['model_attack_count']==6
    directories=[DEFAULT,OUTPUT,AUDIT,SAFETY,ROOT/'artifacts/final-agent-safety-2026-10-25',
      ROOT/'artifacts/final-safety-preflight-2026-10-25',ROOT/'artifacts/final-eval-preflight-2026-10-25',PUBLIC]
    files={}
    for d in directories:
        for p in d.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.name not in ('evidence.zip','evidence.sha256','release-validation.json'):
                files[p.relative_to(ROOT).as_posix()]=p
    for p in (ROOT/'artifacts').glob('final-*.xml'):files[p.relative_to(ROOT).as_posix()]=p
    for name in ('README.md','.env.example','pyproject.toml','LICENSE','NOTICE'):
        p=ROOT/name
        if p.exists():files[name]=p
    for pattern in ('docs/final-agent-*.md','scripts/*final*.py','examples/final-eval-demo/*'):
        for p in ROOT.glob(pattern):
            if p.is_file():files[p.relative_to(ROOT).as_posix()]=p
    secrets={v.encode() for k,v in os.environ.items() if any(w in k.upper() for w in ('API_KEY','TOKEN','SECRET','PASSWORD')) and len(v)>=8}
    for project in (ROOT,ROOT.parent/'pico-main'):
        for p in project.glob('.env*'):
            if p.is_file() and p.name!='.env.example':
                for line in p.read_text('utf-8').splitlines():
                    item=_parse_env_line(line)
                    if item and any(w in item[0].upper() for w in ('API_KEY','TOKEN','SECRET','PASSWORD')) and len(item[1])>=8:secrets.add(item[1].encode())
    canary=load_json(ROOT/'benchmarks/final_agent_v1/security.json')['canary'].encode()
    scanned=0;public_canaries=[]
    def scan(name,body):
        nonlocal scanned
        scanned+=1
        assert not any(secret in body for secret in secrets),'Credential detected: '+name
        assert 'final-private-gold' not in name and not name.endswith(('holdout_references.json','reference_solutions.json')),name
        base=name.rsplit('/',1)[-1]
        if base.startswith('.env') and base!='.env.example':
            assert 'agent-safety' in name or 'safety-preflight' in name,name
            assert body==canary,'Only the exact frozen public security canary may use an .env fixture filename'
            public_canaries.append(name)
        if name.endswith('.zip'):
            with zipfile.ZipFile(BytesIO(body)) as nested:
                for entry in nested.namelist():scan(name+'!'+entry,nested.read(entry))
    hashes={}
    for name,p in sorted(files.items()):
        scan(name,p.read_bytes());hashes[name]=digest(p)
    archive_path=PUBLIC/'evidence.zip'
    with zipfile.ZipFile(archive_path,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,p in sorted(files.items()):archive.write(p,name)
        archive.writestr('EVIDENCE_MANIFEST.json',json.dumps({'sha256':hashes,'credential_scan':'passed',
            'scanned_entries':scanned,'private_reference_solutions_excluded':True,
            'public_fake_env_canaries':public_canaries,'exporter_sha256':digest(Path(__file__))},indent=2))
    (PUBLIC/'evidence.sha256').write_text(digest(archive_path)+'  evidence.zip\n',encoding='ascii',newline='\n')
    write_json(PUBLIC/'export-audit.json',{'at':utcnow(),'archive_bytes':archive_path.stat().st_size,'sha256':digest(archive_path),
       'files':len(hashes),'scanned_including_nested':scanned,'credential_scan':'passed','private_gold_excluded':True,
       'public_fake_env_canaries':len(public_canaries),
       'exporter_note':'Separate release-time exporter includes追加恢复and guarded public fake .env canaries. Frozen runtime, evaluation definitions and raw results unchanged.'},exclusive=True)
    print(json.dumps({'bytes':archive_path.stat().st_size,'sha256':digest(archive_path),'scanned':scanned,'credential_scan':'passed'}))


if __name__=='__main__':main()
