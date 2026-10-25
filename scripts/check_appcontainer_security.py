"""Real Windows isolation probes; no model or repair evaluation reruns."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.windows_sandbox import run_verification,VERIFY
from pico.evaluation.repair_suite import write_json,digest,utcnow


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'artifacts/reliability-v2-2026-10-25/os-security');args=p.parse_args()
    out=args.output.resolve()
    if not out.is_relative_to(ROOT/'artifacts'):p.error('Output must be a new directory under artifacts')
    out.mkdir(parents=True,exist_ok=False);host=out/'host-canary.txt';host.write_text('PUBLIC_OS_CANARY')
    root=out/'workspace';root.mkdir();(root/'.env').write_text('PUBLIC_WORKSPACE_CANARY')
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
    source='''import unittest,os,socket,subprocess,sys
from pathlib import Path
class IsolationTests(unittest.TestCase):
 def test_positive_computation(self):self.assertEqual(2+3,5)
 def test_host_read_denied(self):
  with self.assertRaises(PermissionError):Path(HOST).read_text()
 def test_host_write_denied(self):
  with self.assertRaises(PermissionError):Path(HOST).write_text('changed')
 def test_workspace_secret_not_staged(self):self.assertFalse(Path('.env').exists())
 def test_credential_not_inherited(self):self.assertNotIn('SILICONFLOW_API_KEY',os.environ)
 def test_loopback_denied(self):
  with socket.socket() as s:
   s.settimeout(2)
   with self.assertRaises(PermissionError):s.connect(('127.0.0.1',PORT))
 def test_external_socket_denied(self):
  with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
   with self.assertRaises(PermissionError):s.connect(('192.0.2.1',9))
 def test_child_process_denied(self):
  try:r=subprocess.run([sys.executable,'-c','print(123)'],capture_output=True,timeout=3)
  except OSError as e:self.assertIn(e.winerror,(5,1816))
  else:self.fail('Child process creation was permitted: '+repr(r.returncode))
'''.replace('HOST',repr(str(host))).replace('PORT',str(port))
    (root/'test_acceptance.py').write_text(source,encoding='utf-8')
    try:normal=run_verification(root,VERIFY,20)
    finally:listener.close()
    timed=out/'timeout-workspace';timed.mkdir()
    (timed/'test_acceptance.py').write_text('import unittest,time\nclass Tests(unittest.TestCase):\n def test_sleep(self):time.sleep(10)\n')
    timeout_observed=False
    try:run_verification(timed,VERIFY,1)
    except subprocess.TimeoutExpired:timeout_observed=True
    audits=[json.loads(x.read_text('utf-8')) for x in out.rglob('audit.json')]
    passed=normal['returncode']==0 and timeout_observed and host.read_text()=='PUBLIC_OS_CANARY' and any(a.get('timeout_job_terminated') for a in audits)
    result={'at':utcnow(),'backend':'windows_appcontainer','script_sha256':digest(Path(__file__)),
      'security_tests':8,'normal':normal,'timeout_observed':timeout_observed,'host_unchanged':host.read_text()=='PUBLIC_OS_CANARY',
      'audits':audits,'passed':passed,'no_model_used':True,
      'limits':'OS access-control isolation with private stdlib, no network capability, job limits. Not VM/kernel isolation; normal host backend remains explicitly different. No old13/14 result changed.'}
    write_json(out/'results.json',result,exclusive=True);print(json.dumps(result,ensure_ascii=False))
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
