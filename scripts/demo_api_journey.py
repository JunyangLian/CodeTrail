"""G3 real-HTTP user journey demo: upload -> run -> trace -> report."""
import time

import httpx

GBFF = """LOCUS       TESTD                    60 bp ds-DNA circular SYN 17-OCT-2026
DEFINITION  Test record for V3 API journey.
ACCESSION   TESTD
VERSION     TESTD.1
KEYWORDS    .
SOURCE      Test organism
  ORGANISM  Test organism
            Test phylum
FEATURES             Location/Qualifiers
     source          1..60
                     /organism="Test organism"
     CDS             join(1..30,31..60)
                     /gene=atpA
                     /protein_id="TST_1"
                     /translation="MKVL"
ORIGIN
        1 acgtacgtac gtacgtacgt acgtacgtac gtacgtacgt acgtacgtac gtacgtacgt
//
"""

c = httpx.Client(base_url="http://127.0.0.1:8017", timeout=30)
up = c.post("/api/v1/packets", files={"file": ("demo.gbff", GBFF.encode())}).json()
print("upload  :", up["packet_id"], "(reused)" if up["reused"] else "(new)")
run_id = c.post("/api/v1/jobs", json={
    "packet_id": up["packet_id"], "domain": "vertebrate_mitochondrion",
    "user_goal": "G3 real-HTTP demo"}).json()["run_id"]
print("submitted:", run_id)
for _ in range(100):
    s = c.get(f"/api/v1/jobs/{run_id}").json()
    if s["status"] in ("succeeded", "failed", "budget_exhausted", "cancelled"):
        break
    time.sleep(0.1)
print("status  :", s["status"], "| decision:", s["qc_decision"],
      "| evidence:", s["evidence_validity"])
ev = c.get(f"/api/v1/jobs/{run_id}/events").json()["events"]
print("trace   :", " -> ".join(dict.fromkeys(e["node"] for e in ev)))
md = c.get(f"/api/v1/jobs/{run_id}/report",
           params={"format": "markdown"}).json()["report"]
print("markdown:", md.splitlines()[0], "| chars:", len(md))
