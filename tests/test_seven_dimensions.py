from pico.evaluation.seven_dimensions import run_metrics,ratio,percentile
import json


def test_missing_context_is_measured_against_gold_not_repeat_reduction():
    row={'functional_passed':False,'workflow_passed':False,'verify_command':'test','failure_category':'incomplete',
         'scope_valid':True,'protected_files_unchanged':True,'elapsed_ms':5,'transport_failures':0,'answer':'All tests passed'}
    tools=[{'event':'request','index':1,'name':'read_file','args':{'path':'a.py'}},
           {'event':'response','index':1,'name':'read_file','args':{'path':'a.py'},'output':'   1: return 0',
            'metadata':{'tool_status':'ok'},'model_request_index':1}]
    wire=[{'event':'request','body':json.dumps({'messages':[{'role':'user','content':'read code'}]})},
          {'event':'request','body':json.dumps({'messages':[{'role':'tool','tool_call_id':'read-1','content':'truncated'}]})}]
    gold={'required_files':['a.py','b.py'],'spans':[{'path':'a.py','lines':[{'number':1,'text':'return 0'}]}]}
    m=run_metrics({},row,tools,wire,gold)
    assert m['retrieval']['repeat_reads']['numerator']==0
    assert m['retrieval']['recall_at_k']['3']['value']==.5
    assert m['retrieval']['gold_span_coverage']['value']==0
    assert m['retrieval']['context_loss']['value']==1
    assert m['faithfulness']['false_success_claim'] is True
    assert not m['cost']['tokens']['input']['complete']
    assert m['cost']['actual_currency_cost'] is None


def test_empty_denominators_are_not_perfect_scores():
    assert ratio(0,0)['value'] is None
    assert percentile([], .95) is None
    assert abs(percentile([1,2,3],.95)-2.9)<1e-10
