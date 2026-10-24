"""Materialize one frozen task for CLI/web demonstration without reference patches."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import SUITE, check_freeze, install, load_json

def main():
    tasks={task['id']:task for task in load_json(SUITE/'tasks.json')['tasks']}
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task',choices=tasks,default='boltons_isoparse')
    parser.add_argument('--destination',default='examples/repair-suite-demo')
    args=parser.parse_args()
    destination=(ROOT/args.destination).resolve()
    if ROOT not in destination.parents:
        parser.error('destination must be inside project')
    check_freeze()
    task=tasks[args.task]
    install(destination,task['files'])
    relative=destination.relative_to(ROOT).as_posix()
    print('Prepared frozen buggy task: '+args.task)
    print(f'python -X utf8 -m pico.web --cwd "{relative}" --model-timeout 120 --open')
    print('Task instructions: '+str(destination/'README.md'))
    print('Tests initially fail by design; no reference solution was copied.')

if __name__=='__main__':
    main()
