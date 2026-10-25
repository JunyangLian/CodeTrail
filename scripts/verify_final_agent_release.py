"""Read-only verification of published archive, frozen source and all original/recovery observations."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.report_final_agent_eval import PUBLIC
from scripts.run_final_agent_eval import DEFAULT,check
from scripts.run_final_credential_recovery import OUTPUT
from pico.evaluation.repair_suite import load_json,digest


def main():
    check(DEFAULT);check(OUTPUT)
    archive_path=PUBLIC/'evidence.zip'
    expected=(PUBLIC/'evidence.sha256').read_text('ascii').split()[0]
    assert digest(archive_path)==expected
    with zipfile.ZipFile(archive_path) as archive:
        manifest=json.loads(archive.read('EVIDENCE_MANIFEST.json'))
        for name,h in manifest['sha256'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest()==h,name
            assert digest(ROOT/name)==h,name
    runs=0
    for name in ('run-evidence-manifest.json','recovery-evidence-manifest.json'):
        for files in load_json(PUBLIC/name).values():
            runs+=1
            for path,h in files.items():assert digest(ROOT/path)==h,path
    assert runs==144
    print(json.dumps({'archive_sha256':expected,'archive_files':len(manifest['sha256']),
        'repair_executions_verified':runs,'frozen_source_and_assets':'unchanged','original_and_additional_evidence':'unchanged'},ensure_ascii=False))


if __name__=='__main__':main()
