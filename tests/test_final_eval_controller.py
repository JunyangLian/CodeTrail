"""Synthetic, scripted controller integration; never a formal repair task."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from pico.evaluation.repair_suite import write_json,VERIFY_COMMAND


def test_controller_records_native_wire_and_independently_verifies_final_state(tmp_path,monkeypatch):
    import scripts.run_final_agent_eval as runner
    assets=tmp_path/'assets';assets.mkdir();out=tmp_path/'batch';out.mkdir()
    files={'app.py':'def add(a,b):\n    return a-b\n','README.md':'Synthetic controller fixture',
           'test_acceptance.py':'import unittest\nfrom app import add\nclass Tests(unittest.TestCase):\n    def test_add(self):self.assertEqual(add(2,3),5)\n'}
    task={'id':'synthetic_controller','cohort':'existing','category':'single_file','origin':{'kind':'author_created'},
          'prompt':'Fix add and verify','files':files,'editable_files':['app.py'],'expected_tests':1,
          'sha256':{p:hashlib.sha256(t.encode()).hexdigest() for p,t in files.items()}}
    write_json(assets/'tasks.json',{'tasks':[task]});write_json(assets/'evidence_gold.json',{
        task['id']:{'required_files':['app.py'],'spans':[{'path':'app.py','lines':[{'number':2,'text':'    return a-b'}]}]}})
    slot={'task_id':task['id'],'cohort':'existing','repeat':1,'run_key':'synthetic-r1'}
    write_json(out/'plan.json',[slot]);write_json(out/'freeze.json',{})
    monkeypatch.setattr(runner,'ROOT',tmp_path);monkeypatch.setattr(runner,'ASSETS',assets);monkeypatch.setattr(runner,'check',lambda _:None)
    monkeypatch.setenv('SILICONFLOW_API_KEY','unit-fake-credential')
    actions=[('read_file',{'path':'app.py'}),('patch_file',{'path':'app.py','old_text':'return a-b','new_text':'return a+b'}),
             ('run_shell',{'command':VERIFY_COMMAND}),None]
    class Response:
        headers={'Content-Type':'application/json','x-siliconcloud-trace-id':'synthetic-trace'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):
            action=actions.pop(0);message={'role':'assistant','content':'Tests passed' if action is None else None,'reasoning_content':None}
            if action:
                message['tool_calls']=[{'id':'call-'+str(len(actions)),'type':'function','function':{'name':action[0],'arguments':json.dumps(action[1])}}]
            return json.dumps({'model':runner.MODEL,'choices':[{'message':message,'finish_reason':'tool_calls' if action else 'stop'}],
                               'usage':{'prompt_tokens':10,'completion_tokens':20}}).encode()
    class Opener:
        def open(self,*args,**kwargs):return Response()
    with patch('urllib.request.urlopen'),patch('urllib.request.build_opener',return_value=Opener()):
        runner.worker(out,slot['run_key'])
    directory=out/'runs'/slot['run_key'];row=json.loads((directory/'result.json').read_text('utf-8'))
    assert row['functional_passed'] and row['workflow_passed'] and row['final_matches_last_passing_test']
    assert row['metrics']['cost']['tokens']['input']['observed_total']==40
    assert row['metrics']['retrieval']['gold_span_coverage']['value']==1
    assert 'unit-fake-credential' not in (directory/'wire_io.jsonl').read_text('utf-8')
    assert not list((directory/'workspace').rglob('*reference*'))
