"""Controlled host listeners verify actual TCP/UDP delivery, not socket error spelling.

The earlier errno-only probe is retained as failed. This is a separate observation
protocol with positive host controls; it makes no external Internet-coverage claim.
"""
import argparse
import json
from pathlib import Path
import socket
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.windows_sandbox import run_verification,VERIFY
from pico.evaluation.repair_suite import write_json,utcnow,digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/reliability-v2-2026-10-25/os-network-listeners-v2')
    out=parser.parse_args().output.resolve()
    if not out.is_relative_to(ROOT/'artifacts'):parser.error('Use a new output directory under artifacts')
    out.mkdir(parents=True,exist_ok=False)
    tcp=socket.socket();tcp.bind(('127.0.0.1',0));tcp.listen(8);tcp.settimeout(.1)
    udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('127.0.0.1',0));udp.settimeout(.1)
    received=[];stop=threading.Event()
    def accept():
        while not stop.is_set():
            try:
                c,_=tcp.accept()
                with c:c.settimeout(.3);received.append(('tcp',c.recv(128).decode()))
            except socket.timeout:pass
    def datagrams():
        while not stop.is_set():
            try:received.append(('udp',udp.recvfrom(128)[0].decode()))
            except socket.timeout:pass
    threads=[threading.Thread(target=f,daemon=True) for f in (accept,datagrams)]
    for t in threads:t.start()
    with socket.create_connection(tcp.getsockname()) as s:s.sendall(b'HOST_CONTROL')
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:s.sendto(b'HOST_CONTROL',udp.getsockname())
    time.sleep(.2)
    assert ('tcp','HOST_CONTROL') in received and ('udp','HOST_CONTROL') in received
    workspace=out/'workspace';workspace.mkdir()
    code=f'''import unittest,socket
class Tests(unittest.TestCase):
 def test_tcp_attempt(self):
  with socket.socket() as s:
   s.settimeout(1)
   try:s.connect(('127.0.0.1',{tcp.getsockname()[1]}));s.sendall(b'SANDBOX_PROBE')
   except OSError as e:print('TCP_OBSERVED',type(e).__name__,e.errno)
 def test_udp_attempt(self):
  with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
   try:s.sendto(b'SANDBOX_PROBE',('127.0.0.1',{udp.getsockname()[1]}))
   except OSError as e:print('UDP_OBSERVED',type(e).__name__,e.errno)
'''
    (workspace/'test_acceptance.py').write_text(code)
    write_json(out/'protocol.json',{'at':utcnow(),'script_sha256':digest(Path(__file__)),
      'source_sha256':digest(workspace/'test_acceptance.py'),'criterion':'Host positive controls received for both protocols; sandbox runs both attempts; no sandbox connection/datagram reaches controlled listeners.',
      'limits':'Controlled loopback only; Internet destinations unassessed. Does not replace earlier failed errno-only probe.'},exclusive=True)
    try:
        result=run_verification(workspace,VERIFY,10);time.sleep(.3)
    finally:
        stop.set()
        for t in threads:t.join(1)
        tcp.close();udp.close()
    passed=result['returncode']==0 and not any(v=='SANDBOX_PROBE' for kind,v in received)
    report={'at':utcnow(),'passed':passed,'actual_delivery_events':received,'worker':result,
      'host_controls_passed':True,'internet_egress':'not_evaluated','no_model_used':True}
    write_json(out/'results.json',report,exclusive=True);print(json.dumps(report,ensure_ascii=False))
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
