"""One-off: feed S2 real-trace error candidates into the Data Flywheel queue
(source=s2_real_ab_trace; auto-attribution only -> candidate status; no
promotion, no user-upload data involved)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent.evolution.flywheel import DataFlywheel
from genomeqc_agent.evolution.regression_pool import RegressionPool
from genomeqc_agent.runtime.graph import QCGraph

ROOT = Path(__file__).resolve().parents[1]
S2 = ROOT / "data" / "eval" / "s2_experiment"

rows = [json.loads(l) for l in
        (ROOT / "V3_S2_RUN_LEDGER.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()]
obs = [r for r in rows if r.get("kind") == "observation"]
states = []
graph = QCGraph(db_path=S2 / "checkpoints.sqlite3")
try:
    for r in obs:
        if (r.get("unresolved_pointers") or 0) > 0 or r.get("status") == "failed":
            state = graph.get_state(r["run_id"])
            state["run_id"] = r["run_id"]
            states.append(state)
finally:
    graph.close()

pool = RegressionPool(ROOT / "data" / "regression_pool" / "v3_pool.v1.jsonl")
fw = DataFlywheel(pool, ROOT / "data" / "evolution" / "s2_flywheel")
report = fw.ingest_run_states(states, source="s2_real_ab_trace")
print("flywheel ingest:", json.dumps(report, ensure_ascii=False))
for case in fw.pending_cases():
    print(f"  pending: {case['case_id']} src={case['source']} "
          f"group={case['source_group_id']} mech={case['error_mechanism'][:60]} "
          f"evidence={json.dumps(case['evidence'].get('unresolved', []))}")
