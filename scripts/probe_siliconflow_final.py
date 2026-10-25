"""Non-benchmark provider compatibility probe. Credentials only from the environment."""
import json
import os
from pathlib import Path
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.providers.clients import OpenAICompatibleModelClient
from pico.evaluation.repair_suite import append_json,write_json

def main():
    out=ROOT/'artifacts/final-eval-preflight-2026-10-25'
    client=OpenAICompatibleModelClient('deepseek-ai/DeepSeek-V4.1-Flash','https://api.siliconflow.cn/v1',
                                     os.environ['SILICONFLOW_API_KEY'],.2,180,'chat_completions')
    client.top_p=.95
    tools={'echo_probe':{'schema':{'value':'str'},'description':'Return the input value without changes.'}}
    client.begin_native_run(tools,'Compatibility probe; no repository or repair task.',
                            'Call echo_probe with value=probe-verified, then after its result reply probe-verified.',None)
    client.on_transport_event=lambda e:append_json(out/'probe-transport.jsonl',e)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    original=urllib.request.urlopen;urllib.request.urlopen=opener.open
    records=[]
    try:
        for i in range(3):
            request=client.native_conversation.audit_request()
            reply=client.complete('not sent in native mode',4096)
            records.append({'index':i+1,'request':request,'reply':str(reply),'kind':reply.action_kind,
                            'metadata':dict(client.last_completion_metadata)})
            if reply.action_kind=='tool':
                assert reply.action=={'name':'echo_probe','args':{'value':'probe-verified'}}
                client.observe_native_tool('probe-verified')
            elif reply.action_kind=='retry':client.native_notice(str(reply))
            else:break
        assert any(r['kind']=='tool' for r in records) and records[-1]['kind']=='final'
        result={'compatible':True,'scope':'Non-benchmark tool protocol only; no repair or holdout task.',
                'model':'deepseek-ai/DeepSeek-V4.1-Flash','records':records}
    except Exception as exc:
        result={'compatible':False,'error_type':type(exc).__name__,'error':str(exc),'records':records}
    finally:urllib.request.urlopen=original
    write_json(out/'compatibility-probe-v2.json',result,exclusive=True)
    print(json.dumps({'compatible':result['compatible'],'turns':len(records),'error':result.get('error'),
                      'models':[r['metadata'].get('provider_model') for r in records]},ensure_ascii=False))

if __name__=='__main__':main()
