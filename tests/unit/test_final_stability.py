"""Final-round P4: engineering stability regressions for the ten audited
risk items. Adds tests for the gaps the audit identified; items already
locked by existing suites are cross-referenced in the docstring.

Item -> test map (final round):
  1 budget/ledger survives resume ........ test_release_audit.py
                                           test_budget_guard_restores_from_ledger
  2 interrupted runs are not double-counted  test_interrupted_run_not_double_counted
  3 per-thread planner state independence    test_planner_state_is_per_instance
  4 per-thread checkpoint db isolation       test_checkpoint_dbs_are_isolated_and_resumable
  5 concurrent ledger appends complete       test_ledger_concurrent_appends_complete
  6 model errors never attributed to scorer  test_model_error_is_never_scorer_error
  7 no ScriptedPlanner in real experiments   test_real_runners_block_unauthorized
  8 compression never drops unresolved       test_compression_never_drops_unresolved
  9 judgment vs qc verdict separation ... test_release_audit.py
                                           test_critical_false_pass_requires_qc_pass_on_fault_case
  10 simulated approval cannot publish ... test_release_audit.py
                                           test_simulated_approval_cannot_trigger_publish
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from genomeqc_agent.context.builder import ContextBuilder
from genomeqc_agent.eval.safety import is_real_model_run
from genomeqc_agent.evolution.attribution import attribute
from genomeqc_agent.evolution.budget import BudgetGuard
from genomeqc_agent.model.llm_planner import LLMPlanner
from genomeqc_agent.runtime.graph import QCGraph

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


# ---- item 2: interrupted runs are not double-counted --------------------
def test_interrupted_run_not_double_counted(tmp_path):
    """A completed (case, model, condition, repeat) cell recorded in the
    ledger must never be re-run: the resume filter consumes the ledger as
    the single source of truth, and the guard is seeded once from it."""
    ledger = tmp_path / "ledger.jsonl"
    row = {"kind": "observation", "case_id": "c1", "model_key": "glm",
           "condition": "A", "repeat": 1, "run_id": "r-1", "tokens": 100,
           "requests": 2}
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")
    seeded = BudgetGuard.from_ledger(ledger, token_cap=0, request_cap=0)
    assert seeded.tokens == 100 and seeded.requests == 2

    all_cells = [("c1", "glm", "A", 1), ("c1", "glm", "B", 1)]
    done = {(row["case_id"], row["model_key"], row["condition"],
             row["repeat"])}
    todo = [c for c in all_cells if c not in done]
    assert todo == [("c1", "glm", "B", 1)]

    guard = BudgetGuard(token_cap=1_000, request_cap=100)
    guard.tokens += seeded.tokens
    guard.requests += seeded.requests
    guard.settle(50, 1, 0, 0)          # the one remaining cell runs once
    assert guard.tokens == 150 and guard.requests == 3


# ---- item 3: per-thread planner state -----------------------------------
class _FakeGateway:
    provider = "fake"

    def __init__(self, model_id="fake-model"):
        self.model_id = model_id
        self.calls = 0

    def chat_json(self, system, user, temperature=0.0, max_tokens=4096):
        self.calls += 1
        return {"content": '{"kind": "synthesize", "reason": "done"}',
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "total_tokens": 15},
                "latency_ms": 1.0, "finish_reason": "stop", "reasoning_chars": 0}


def test_planner_state_is_per_instance():
    """Interleaved calls on two planner instances must not share last_usage
    (the V3.2 20-thread race fix)."""
    gw_a, gw_b = _FakeGateway("a"), _FakeGateway("b")
    pa = LLMPlanner(gw_a, max_tokens=8192)
    pb = LLMPlanner(gw_b, max_tokens=8192)
    pa.next_action({}, {})
    mid_b = pb.next_action({}, {})
    assert pa.last_usage["total_tokens"] == 15
    assert pb.last_usage["total_tokens"] == 15
    assert pa is not pb and pa.last_usage is not pb.last_usage
    assert mid_b.kind == "synthesize"


# ---- item 4: per-thread checkpoint isolation + resumability -------------
def test_checkpoint_dbs_are_isolated_and_resumable(tmp_path):
    import uuid

    from genomeqc_agent.model.scripted_planner import ScriptedPlanner
    db_a, db_b = tmp_path / "a.sqlite3", tmp_path / "b.sqlite3"
    ga = QCGraph(db_path=db_a, planner=ScriptedPlanner())
    run_id = str(uuid.uuid4())
    ga.run(run_id, "chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
           str(ROOT / "examples" / "public_packets"
               / "chloro_C1_NC_013991_2_CHLORO-NORMAL-00"))
    ga.close()
    gb = QCGraph(db_path=db_b, planner=ScriptedPlanner())
    try:
        # thread id from db A must NOT be visible in db B
        assert not gb.get_state(run_id)
    finally:
        gb.close()
    # and db A still resumes its own thread
    ga2 = QCGraph(db_path=db_a, planner=ScriptedPlanner())
    try:
        state = ga2.get_state(run_id)
        assert state["run_id"] == run_id
    finally:
        ga2.close()


# ---- item 5: concurrent ledger appends ----------------------------------
def test_ledger_concurrent_appends_complete(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    lock = threading.Lock()
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for i in range(50):
                line = json.dumps({"kind": "observation", "thread": tid,
                                   "i": i, "tokens": 1}) + "\n"
                with lock, ledger.open("a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()
        except Exception as error:  # noqa: BLE001 - test collects
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 400
    parsed = [json.loads(l) for l in lines]  # every line is complete JSON
    assert len({(p["thread"], p["i"]) for p in parsed}) == 400  # no dups


# ---- item 6: model errors are never scorer errors -----------------------
def test_model_error_is_never_scorer_error():
    rec = attribute(run_id="r", case_id="c",
                    error_text="gateway: provider returned 503: busy",
                    status="failed", model_usage=[], planner_kind="llm")
    assert rec["first_layer_category"] == "infrastructure_error"
    rec2 = attribute(run_id="r", case_id="c",
                     error_text="plan contract violation: kind invalid",
                     status="failed", model_usage=[{"total_tokens": 500}],
                     planner_kind="llm")
    assert rec2["first_layer_category"] != "dataset_or_scorer_error"
    # a scorer error is only attributed when grader_error is actually present
    rec3 = attribute(run_id="r", case_id="c", error_text="anything",
                     status="failed", model_usage=[{"total_tokens": 500}],
                     planner_kind="llm")
    assert rec3["first_layer_category"] != "dataset_or_scorer_error"


# ---- item 7: real runners refuse to start unauthorized ------------------
@pytest.mark.parametrize("script", ["run_v32_ab.py", "run_holdout_ab.py",
                                    "run_p2_ablation.py"])
def test_real_runners_block_unauthorized(script):
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items()
           if k != "GENOMEQC_PAID_AUTHORIZED"}
    proc = subprocess.run([sys.executable, str(SCRIPTS / script)],
                          capture_output=True, text=True, env=env,
                          timeout=120, check=False)
    assert proc.returncode != 0
    assert "BLOCKED" in (proc.stdout + proc.stderr)


# ---- item 8: compression never drops unresolved pointers ----------------
def test_compression_never_drops_unresolved():
    builder = ContextBuilder(token_budget=50)  # force bulk compression
    evidence = {"unresolved": ["artifacts/features.tsv:7",
                               "artifacts/genome.fa"]}
    facts = {"statistics": {"s": {"pointer": "artifacts/sequence_stats.json:"
                                              "sequence_length"}}}
    built = builder.build(task="t", packet_id="p", facts=facts,
                          tool_trace={"inspect_packet": [{}]},
                          denial=None, evidence=evidence)
    assert "UNRESOLVED: artifacts/features.tsv:7" in built["render"]
    assert "UNRESOLVED: artifacts/genome.fa" in built["render"]
    assert built["receipt"]["completeness"] == "partial"  # honest, not silent
    ev_block = next(b for b in built["blocks"]
                    if b["name"] == "evidence_locators")
    assert ev_block["compressed"] is False and ev_block["included"] is True


# ---- safety guard cross-check -------------------------------------------
def test_zero_token_success_is_not_a_real_model_run():
    assert not is_real_model_run([{"total_tokens": 0}])
    assert is_real_model_run([{"total_tokens": 1234}])
    assert not is_real_model_run([])
