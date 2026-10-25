"""Re-run the SAME frozen 14 guards with AppContainer; old results remain unchanged."""
import argparse
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import scripts.run_final_security as old
from pico.runtime import Pico
from pico.evaluation.repair_suite import write_json,load_json,digest,utcnow
from pico.windows_sandbox import VERIFY


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/reliability-v2-2026-10-25/frozen-guard-appcontainer')
    out=parser.parse_args().output.resolve()
    if not out.is_relative_to(ROOT/'artifacts'):parser.error('Use a new output directory under artifacts')
    out.mkdir(parents=True,exist_ok=False)
    asset=load_json(old.ASSET)
    write_json(out/'protocol.json',{'at':utcnow(),'guard_asset_sha256':digest(old.ASSET),'script_sha256':digest(Path(__file__)),
      'runtime_sha256':digest(ROOT/'pico/windows_sandbox.py'),'backend':'appcontainer',
      'policy':'Same 14 frozen cases, criteria, canaries and application approvals. Different execution backend; no old result or criterion replaced. Positive functional and OS boundary probes separately retained.'},exclusive=True)
    def isolated(*args,**kwargs):return Pico(*args,**kwargs,verification_command=VERIFY,shell_backend='appcontainer')
    with patch.object(old,'Pico',side_effect=isolated):rows=old.guard_cases(out,asset)
    result={'at':utcnow(),'backend':'appcontainer','evaluated':sum(r['evaluated'] for r in rows),
      'passed':sum(r.get('passed',False) for r in rows),'rows':rows,'no_model_used':True,
      'limits':'13 guards primarily test existing application checks; the approved-code case now runs in a filtered private AppContainer. 14/14 is not arbitrary-code or VM-security proof.'}
    write_json(out/'results.json',result,exclusive=True)
    print(json.dumps({'evaluated':result['evaluated'],'passed':result['passed']}))
    if result['evaluated']!=14 or result['passed']!=14:raise SystemExit(1)


if __name__=='__main__':main()
