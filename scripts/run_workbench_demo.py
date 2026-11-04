"""Launch the complete small workbench in a fresh, isolated demo workspace."""
import argparse
import hashlib
import json
from datetime import datetime
import os
from pathlib import Path
import shutil
import sys
import uuid
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pico.providers.clients import OpenAICompatibleModelClient
from pico.web import Workbench, make_server
from pico.windows_sandbox import VERIFY
from workbench_demo_support import ScriptedRepairClient


def prepare(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Demo output must be a new directory; historical workspaces are never overwritten')
    shutil.copytree(Path(__file__).resolve().parents[1]/'examples/evolving-repo', destination)
    internal = destination / '.pico'; internal.mkdir()
    (internal/'demo.json').write_text(json.dumps({'type':'codetrail-evolving-demo', 'workspace':str(destination),
        'acceptance_sha256':hashlib.sha256((destination/'test_directory.py').read_bytes()).hexdigest()}), encoding='utf-8')
    (internal/'policy.json').write_text(json.dumps({'read_only_paths':['test_directory.py','users.py','AGENTS.md','README.md','.codetrail']}), encoding='utf-8')
    return destination


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/evolving-repo-demo/'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:5])
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--reuse-workspace', action='store_true', help='Restart only a previously created demo without overwriting files')
    parser.add_argument('--offline', action='store_true', help='Explicit scripted fixture, zero LLM API calls')
    parser.add_argument('--embedding', choices=('off','siliconflow'), default='off')
    parser.add_argument('--network-route', choices=('environment','direct'), default='environment')
    parser.add_argument('--shell-backend', choices=('appcontainer','host'), default='appcontainer')
    parser.add_argument('--open', action='store_true')
    args=parser.parse_args()
    if args.reuse_workspace:
        root=Path(args.output).resolve()
        marker=json.loads((root/'.pico/demo.json').read_text('utf-8'))
        if (marker.get('type')!='codetrail-evolving-demo' or marker.get('workspace')!=str(root) or
                hashlib.sha256((root/'test_directory.py').read_bytes()).hexdigest()!=marker['acceptance_sha256']):
            raise ValueError('Demo identity/acceptance changed; refusing implicit reset')
    else:
        root=prepare(args.output)
    def model():
        if args.offline:
            return ScriptedRepairClient()
        key=os.environ.get('SILICONFLOW_API_KEY','')
        if not key: raise ValueError('Set SILICONFLOW_API_KEY locally; no other credential or model is substituted')
        client=OpenAICompatibleModelClient('deepseek-ai/DeepSeek-V4.1-Flash','https://api.siliconflow.cn/v1',key,.2,180,'chat_completions')
        client.top_p=.95
        return client
    workbench=Workbench(root,verification_command=VERIFY,model_factory=model,shell_backend=args.shell_backend,
                        max_new_tokens=4096,harness_profile='workbench',embedding_mode=args.embedding,network_route=args.network_route)
    original=workbench.factory
    def factory(*values):
        agent=original(*values)
        if args.offline: agent.model_client.agent=agent
        return agent
    workbench.factory=factory
    server=make_server(workbench,args.port)
    url=f'http://127.0.0.1:{server.server_port}/'
    print(f'CodeTrail workbench: {url}\nWorkspace: {root}\nMode: {"SCRIPTED / NO LLM" if args.offline else "SiliconFlow DeepSeek-V4.1-Flash"}\nBackend: {args.shell_backend}',flush=True)
    if args.open:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
