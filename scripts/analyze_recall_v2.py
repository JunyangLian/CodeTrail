"""Read-only diagnosis of published Recall@5; never changes gold or reports gains."""
import ast
import csv
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import load_json,write_json,digest,utcnow
from pico.evaluation.seven_dimensions import ratio

PUBLIC=ROOT/'benchmarks/results/reliability-v2-2026-10-25'


def import_closure(files):
    modules={p[:-3].replace('/','.') :p for p in files if p.endswith('.py')}
    reached=set();pending=['test_acceptance.py']
    while pending:
        name=pending.pop()
        if name in reached:continue
        reached.add(name)
        for node in ast.walk(ast.parse(files[name])):
            imports=([a.name for a in node.names] if isinstance(node,ast.Import) else
                     [node.module] if isinstance(node,ast.ImportFrom) and node.module else [])
            pending.extend(modules[n] for n in imports if n in modules and modules[n] not in reached)
    return reached


def main():
    PUBLIC.mkdir(parents=True,exist_ok=True)
    old=ROOT/'benchmarks/results/final-agent-90-2026-10-25'
    first=load_json(old/'per-run.json');extra=load_json(old/'additional-per-run.json');rows=first+extra
    goldpath=ROOT/'benchmarks/final_agent_v1/evidence_gold.json';gold=load_json(goldpath)
    expected=load_json(old/'audit.json')['gold_sha256'];assert digest(goldpath)==expected and len(rows)==144
    groups={}
    for name,rs in {'all144':rows,'first90':first,'additional54':extra,
                  'non_billing90':[r for r in rows if 'HTTP 402' not in r.get('error','')]}.items():
        metrics=[r['metrics']['retrieval']['recall_at_k']['5'] for r in rs]
        groups[name]={'runs':len(rs),'legacy_recall_at_5':ratio(sum(m['numerator'] for m in metrics),sum(m['denominator'] for m in metrics))}
    tasks={t['id']:t for t in load_json(ROOT/'benchmarks/final_agent_v1/tasks.json')['tasks']}
    problems=[]
    for name in ('boltons_isoparse','boltons_xfrange','boltons_multireplace','boltons_splitlines'):
        task=tasks[name];closure=import_closure(task['files']);required=set(gold[name]['required_files'])
        problems.append({'task_id':name,'frozen_required_files':sorted(required),'editable_files':task['editable_files'],
           'static_local_import_closure_from_tests':sorted(closure),'required_but_outside_import_closure':sorted(required-closure),
           'finding':'Editable scope was mistakenly treated as necessary evidence; config/health/validation distractors have no static path from these visible tests. This diagnosis does not replace frozen gold or re-score the experiment.'})
    misses=[]
    for batch,rs in [('first90',first),('additional54',extra)]:
        for r in rs:
            m=r['metrics']['retrieval']['recall_at_k']['5'];ordered=r['metrics']['retrieval']['retrieved_files'];required=set(gold[r['task_id']]['required_files'])
            if 'HTTP 402' not in r.get('error','') and m['numerator']<m['denominator']:
                misses.append({'batch':batch,'run_key':r['run_key'],'task_id':r['task_id'],'functional_passed':r['functional_passed'],
                   'hits':m['numerator'],'denominator':m['denominator'],'first_five_files':ordered[:5],
                   'missing_at_5':sorted(required-set(ordered[:5])),'read_later':sorted(required & set(ordered[5:])),
                   'not_read':sorted(required-set(ordered))})
    result={'at':utcnow(),'script_sha256':digest(Path(__file__)),'historical_gold_sha256':expected,'historical_gold_unchanged':True,
      'groups':groups,'annotation_errors':problems,'non_billing_misses':misses,
      'meaning':'Published metric counts necessary-file coverage among the first five DISTINCT successful read/search file observations in an Agent sequence. It is not the Top-5 quality of a standalone ranked retriever.',
      'limits':['Non-billing slice is descriptive only; all54 billing failures remain in primary results.',
         'No replacement gold, corrected score, Agent rerun, new task, or measured retrieval improvement is claimed.',
         'Static import closure is diagnostic evidence for these four small fixtures, not an automatic universal necessary-evidence gold.',
         'Two dotenv_override runs omitted a predeclared variables.py contract file despite functional success; passing tests do not prove evidence completeness.']}
    write_json(PUBLIC/'recall-diagnosis.json',result,exclusive=True)
    with (PUBLIC/'retrieval-misses.csv').open('x',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(misses[0]),lineterminator='\n');writer.writeheader();writer.writerows(misses)
    print(json.dumps({'groups':groups,'annotation_error_tasks':len(problems),'non_billing_missed_runs':len(misses)},ensure_ascii=False))


if __name__=='__main__':main()
