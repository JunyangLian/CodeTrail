"""Run the public frozen evaluation without access to private reference patches."""
import argparse
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.run_final_agent_eval import DEFAULT,check,run
from pico.evaluation.repair_suite import write_json,digest,utcnow


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--prepare-only',action='store_true');args=p.parse_args()
    output=args.output.resolve()
    if ROOT not in output.parents or 'artifacts' not in output.relative_to(ROOT).parts:p.error('Use a new output directory under this repository/artifacts.')
    if not (DEFAULT/'freeze.json').exists():p.error('Extract published evidence.zip into the repository root first.')
    check(DEFAULT)
    output.mkdir(parents=True,exist_ok=False)
    for name in ('freeze.json','plan.json','protocol.json','source_snapshot.zip','private-gold-custody.json'):
        shutil.copyfile(DEFAULT/name,output/name)
    write_json(output/'public-reproduction.json',{'at':utcnow(),'original_freeze_sha256':digest(DEFAULT/'freeze.json'),
        'reference_patch_access':False,'policy':'Public frozen source/tasks/tests and exact original schedule. No private patch required. New observations never replace published results.'},exclusive=True)
    check(output)
    print('Public frozen inputs verified; no reference patch loaded.',flush=True)
    if not args.prepare_only:run(output)


if __name__=='__main__':main()
