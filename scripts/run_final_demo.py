"""Start the existing local workbench using only SILICONFLOW_API_KEY, never dotenv."""
import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
import urllib.request

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico import web
from scripts.run_final_agent_eval import client,CONFIG


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765);parser.add_argument('--check',action='store_true')
    parser.add_argument('--host-shell',action='store_true',help='Explicitly use the host backend without OS isolation; default uses AppContainer.')
    args=parser.parse_args()
    if not os.environ.get('SILICONFLOW_API_KEY'):parser.error('Set SILICONFLOW_API_KEY in the local process environment; no fallback key or model.')
    urllib.request.urlopen=urllib.request.build_opener(urllib.request.ProxyHandler({})).open
    with patch('pico.web.load_project_env',return_value={}),patch('pico.web._build_model_client',side_effect=lambda args:client()),patch.dict(os.environ,{'PICO_SECRET_ENV_NAMES':'SILICONFLOW_API_KEY'}):
        app=web.Workbench(ROOT/'examples/final-eval-demo',verification_command=CONFIG['verification_command'],model_timeout=180,max_new_tokens=4096,shell_backend='host' if args.host_shell else 'appcontainer')
        server=web.make_server(app,0 if args.check else args.port)
        if args.check:
            try:
                config=app.config()
                assert config['model']==CONFIG['model'] and app.model.base_url==CONFIG['base_url']
                assert config['configured'] and app.model.top_p==CONFIG['top_p']
                print(json.dumps({k:config[k] for k in ('workspace_name','model','configured','max_new_tokens','verification_command','shell_backend')},ensure_ascii=False))
                print('Demo configuration validated; no model call, dotenv load or credential output.')
            finally:server.server_close()
            return
        print('CodeTrail final demo: http://127.0.0.1:'+str(server.server_port),flush=True)
        print('Model: '+app.model.model+'; environment-only credential; approvals remain interactive.',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()


if __name__=='__main__':main()
