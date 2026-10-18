"""One-off patch: per-cell strategy features for the V3.2 A/B runner."""
from pathlib import Path

p = Path("scripts/run_v32_ab.py")
t = p.read_text(encoding="utf-8")

old1 = """            run_id = str(uuid.uuid4())
            graph = QCGraph(db_path=OUT / "checkpoints.sqlite3",
                            out_dir=OUT / "runs",
                            planner=planners[(model_key, cond)])
            try:
                try:
                    state = graph.run(run_id, case_id,
                                      str(V2_CANDIDATES / case_id),
                                      max_model_requests=MAX_REQUESTS_PER_RUN,
                                      strategy=strategy)"""
new1 = """            run_id = str(uuid.uuid4())
            cell_strategy = {**strategy,
                             "features": {"context_builder_v1":
                                          cond == "B"}}
            graph = QCGraph(db_path=OUT / "checkpoints.sqlite3",
                            out_dir=OUT / "runs",
                            planner=planners[(model_key, cond)])
            try:
                try:
                    state = graph.run(run_id, case_id,
                                      str(V2_CANDIDATES / case_id),
                                      max_model_requests=MAX_REQUESTS_PER_RUN,
                                      strategy=cell_strategy)"""
assert old1 in t, "block1"
t = t.replace(old1, new1)

old2 = """            state = graph.run(run_id, r["case_id"],
                              str(V2_CANDIDATES / r["case_id"]),
                              max_model_requests=MAX_REQUESTS_PER_RUN,
                              strategy=strategy)"""
new2 = """            cell_strategy = {**strategy,
                             "features": {"context_builder_v1":
                                          r["condition"] == "B"}}
            state = graph.run(run_id, r["case_id"],
                              str(V2_CANDIDATES / r["case_id"]),
                              max_model_requests=MAX_REQUESTS_PER_RUN,
                              strategy=cell_strategy)"""
assert old2 in t, "block2"
t = t.replace(old2, new2)

p.write_text(t, encoding="utf-8")
print("patched")
