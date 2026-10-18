"""P2.8 acceptance: full user journey over real HTTP (upload -> run ->
status -> events -> JSON report -> Markdown download)."""
import json
import time

import httpx

c = httpx.Client(base_url="http://127.0.0.1:8021", timeout=60)
steps = []

gb = next(iter((n := __import__("pathlib").Path(
    r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2\data\organelle_v2\normalized"
)).glob("*/canonical.gbff")))
up = c.post("/api/v1/packets", files={"file": ("acceptance.gbff", gb.read_bytes())}).json()
steps.append(["upload", up["packet_id"]])

run_id = c.post("/api/v1/jobs", json={
    "packet_id": up["packet_id"], "domain": "vertebrate_mitochondrion",
    "user_goal": "P2.8 acceptance"}).json()["run_id"]
steps.append(["submit", run_id])

for _ in range(300):
    s = c.get(f"/api/v1/jobs/{run_id}").json()
    if s["status"] in ("succeeded", "failed", "cancelled", "budget_exhausted"):
        break
    time.sleep(0.15)
steps.append(["status", s["status"]])

ev = c.get(f"/api/v1/jobs/{run_id}/events").json()["events"]
steps.append(["events", len(ev)])
nodes = list(dict.fromkeys(e["node"] for e in ev))

rep = c.get(f"/api/v1/jobs/{run_id}/report").json()
steps.append(["report_json", rep["report"]["qc_decision"]])
md = c.get(f"/api/v1/jobs/{run_id}/report", params={"format": "markdown"})
steps.append(["report_md_download", [md.status_code, len(md.text)]])

print(json.dumps({"steps": steps, "run_id": run_id, "graph_nodes": nodes},
                 ensure_ascii=False, default=str))
