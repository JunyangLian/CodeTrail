"""Replay saved model-produced code in clean before/after copies without an API call."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import SUITE,check_freeze,digest,install,load_json,verify_pristine,write_json

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',default='artifacts/repair-suite-v1-2026-10-24/runs/02-boltons_isoparse-r3')
    parser.add_argument('--destination',default='artifacts/repair-suite-replay-demo')
    args=parser.parse_args()
    source=(ROOT/args.run).resolve();destination=(ROOT/args.destination).resolve()
    if ROOT not in source.parents or ROOT not in destination.parents:
        parser.error('paths must be inside project')
    check_freeze()
    result=load_json(source/'result.json')
    task=next(task for task in load_json(SUITE/'tasks.json')['tasks'] if task['id']==result['task_id'])
    assert result['functional_passed'],'Choose a saved functional repair for before/after replay'
    destination.mkdir(parents=True,exist_ok=False)
    implementation={name:(source/'workspace'/name).read_bytes() for name in task['editable_files']}
    for name,data in implementation.items():
        import hashlib
        assert hashlib.sha256(data).hexdigest()==result['final_sha256'][name]
    before=verify_pristine(task,{},destination/'before')
    after=verify_pristine(task,implementation,destination/'after')
    assert not before['accepted'] and after['accepted']
    write_json(destination/'replay.json',{'kind':'saved model patch replay; zero new model runs',
               'source':source.relative_to(ROOT).as_posix(),'source_record_sha256':digest(source/'result.json'),
               'task_id':task['id'],'before':before,'after':after},exclusive=True)
    print('Before: '+str(before['tests_run'])+' tests, expected failure')
    print('After: '+str(after['tests_run'])+' tests, all passed')
    print('No API call; saved model-produced implementation replayed. Evidence: '+str(destination/'replay.json'))

if __name__=='__main__':main()
