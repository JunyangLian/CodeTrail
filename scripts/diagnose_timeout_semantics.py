"""Local loopback reproductions of socket idle timeout versus total wall time."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pico.providers.clients import OpenAICompatibleModelClient


@contextmanager
def fixture_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            body=json.dumps({'choices':[{'message':{'content':'<final>OK</final>'}}],
                'usage':{'prompt_tokens':1,'completion_tokens':1}}).encode()
            try:
                if self.path=='/late-headers':
                    time.sleep(.7)
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)+12 if self.path.endswith('/chat/completions') else len(body)))
                self.end_headers()
                if self.path=='/late-body':
                    time.sleep(.7)
                if self.path.endswith('/chat/completions'):
                    for _ in range(12):
                        self.wfile.write(b' ');self.wfile.flush();time.sleep(.1)
                self.wfile.write(body);self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        yield 'http://127.0.0.1:'+str(server.server_port)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def run_lab():
    rows=[]
    # Explicitly bypass external proxies for the localhost-only fixture.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with fixture_server() as host:
        for route in ('/late-headers','/late-body'):
            start=time.monotonic();phase='headers'
            try:
                request=urllib.request.Request(host+route,data=b'{}',method='POST')
                with opener.open(request,timeout=.25) as response:
                    phase='body';response.read()
                row={'ok':True}
            except TimeoutError as exc:
                row={'ok':False,'error_type':type(exc).__name__}
            rows.append(dict(case=route,phase=phase,timeout_seconds=.25,
                elapsed_ms=round((time.monotonic()-start)*1000),**row))
        client=OpenAICompatibleModelClient('loopback',host,'',.2,.4,'chat_completions')
        start=time.monotonic();text=client.complete('loopback fixture',1)
        rows.append({'case':'periodic-body','timeout_seconds':.4,'nominal_retry_budget_seconds':.8,
            'elapsed_ms':round((time.monotonic()-start)*1000),'ok':text=='<final>OK</final>',
            'transport_attempts':client.last_completion_metadata['transport_attempts']})
    return {'kind':'Local loopback diagnostic; zero provider calls',
        'finding':'Blocking-operation idle timeout and retry-start checks do not enforce a total wall deadline',
        'rows':rows}


if __name__=='__main__':
    output=ROOT/'artifacts/api-timeout-diagnosis-2026-10-25/local-timeout-lab.json'
    result=run_lab()
    with output.open('x',encoding='utf-8',newline='\n') as f:
        json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(result,ensure_ascii=False))
