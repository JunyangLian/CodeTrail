"""G2 LangGraph runtime: routing, bounded recovery, budgets, checkpoint resume."""
from __future__ import annotations

import uuid
from pathlib import Path

from genomeqc_agent.runtime.graph import QCGraph
from genomeqc_agent.tools.registry import TransientToolError

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "public_packets"
NORMAL = EXAMPLES / "chloro_C1_NC_013991_2_CHLORO-NORMAL-00"
FAULT = EXAMPLES / "chloro_C1_NC_013991_2_CHLORO-CDS-03"
MULTI = EXAMPLES / "chloro_C1_NC_013991_2_CHLORO-MULTI-01_SYNTHETIC"


def _run(packet: Path, packet_id: str, **kwargs) -> dict:
    graph = QCGraph(**kwargs)
    try:
        return graph.run(str(uuid.uuid4()), packet_id, str(packet))
    finally:
        graph.close()


def test_normal_packet_routes_verify_only_and_passes():
    state = _run(NORMAL, "chloro_C1_NC_013991_2_CHLORO-NORMAL-00")
    assert state["runtime"]["status"] == "succeeded"
    assert state["result"]["qc_decision"] == "pass"
    assert state["result"]["evidence_validity"] == "valid"
    trace = state["tool_trace"]
    assert "lookup_feature" not in trace  # route A: no flagged features
    assert "verify_evidence_pointer" in trace
    nodes = [e["node"] for e in state["events"]]
    assert nodes[:4] == ["intake_validate", "build_public_packet",
                         "deterministic_mandatory_checks", "plan_next_action"]


def test_multipart_fault_routes_through_lookup_feature():
    state = _run(MULTI, "chloro_C1_NC_013991_2_CHLORO-MULTI-01_SYNTHETIC")
    assert state["result"]["qc_decision"] == "block"
    trace = state["tool_trace"]
    assert trace["lookup_feature"] == [{"feature_id": "CDS_001"}]  # route B
    nodes = [e["node"] for e in state["events"]]
    assert "bounded_recovery" not in nodes
    # the planner reason must be recorded on the audit trail
    plan_events = [e for e in state["events"] if e["node"] == "plan_next_action"]
    assert any("lookup_feature" in e["summary"] for e in plan_events)


def test_two_real_tool_routes_are_distinct():
    """G2 gate: at least two genuinely different tool routes."""
    normal = _run(NORMAL, "chloro_C1_NC_013991_2_CHLORO-NORMAL-00")
    multi = _run(MULTI, "chloro_C1_NC_013991_2_CHLORO-MULTI-01_SYNTHETIC")
    route_a = sorted(normal["tool_trace"])
    route_b = sorted(multi["tool_trace"])
    assert route_a != route_b
    assert "lookup_feature" in route_b and "lookup_feature" not in route_a


def test_intake_rejection_is_terminal_without_tools():
    graph = QCGraph()
    try:
        state = graph.run(str(uuid.uuid4()), "", "does-not-matter")
    finally:
        graph.close()
    assert state["runtime"]["status"] == "failed"
    assert "intake rejected" in state["runtime"]["last_error"]
    assert [e["node"] for e in state["events"]] == ["handle_failure"]


def test_budget_exhaustion_is_terminal():
    graph = QCGraph()
    try:
        state = graph.run(str(uuid.uuid4()), "chloro_C1_NC_013991_2_CHLORO-CDS-03",
                          str(FAULT), max_tool_calls=2)
    finally:
        graph.close()
    assert state["runtime"]["status"] == "budget_exhausted"
    assert state["result"]["qc_decision"] is None  # no report produced
    assert state["budgets"]["tool_calls"] >= 2  # mandatory checks consumed it


class _Flaky:
    """Raises TransientToolError for the first `failures` calls, then delegates."""

    def __init__(self, inner, failures: int):
        self.inner, self.failures = inner, failures

    def __call__(self, port, files, args):
        if self.failures > 0:
            self.failures -= 1
            raise TransientToolError("simulated transient transport failure")
        return self.inner(port, files, args)


def _registry_with_flaky_inspect(failures: int):
    from genomeqc_agent.jobs.smoke_pipeline import default_registry
    registry = default_registry()
    spec = registry.spec("inspect_packet")
    spec.runner = _Flaky(spec.runner, failures)
    return registry


def test_transient_error_recovers_within_bounds():
    registry = _registry_with_flaky_inspect(failures=1)
    graph = QCGraph(registry=registry)
    try:
        state = graph.run(str(uuid.uuid4()), "chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
                          str(NORMAL))
    finally:
        graph.close()
    assert state["runtime"]["status"] == "succeeded"
    assert state["runtime"]["retry_counters"]["inspect_packet"] == 1
    nodes = [e["node"] for e in state["events"]]
    assert nodes.count("bounded_recovery") == 1
    assert "simulated transient transport failure" in " ".join(
        e["summary"] for e in state["events"])


def test_persistent_transient_error_stops_after_limit():
    registry = _registry_with_flaky_inspect(failures=99)
    graph = QCGraph(registry=registry)
    try:
        state = graph.run(str(uuid.uuid4()), "chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
                          str(NORMAL), max_retries_per_tool=1)
    finally:
        graph.close()
    assert state["runtime"]["status"] == "failed"
    assert state["runtime"]["retry_counters"]["inspect_packet"] == 1
    assert "mandatory check failed" in state["runtime"]["last_error"]
    assert "transient_failure" in state["runtime"]["last_error"]


def test_mandatory_failure_cannot_be_skipped_into_a_report():
    registry = _registry_with_flaky_inspect(failures=99)
    graph = QCGraph(registry=registry)
    try:
        state = graph.run(str(uuid.uuid4()), "chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
                          str(NORMAL))
    finally:
        graph.close()
    assert state["runtime"]["status"] == "failed"
    assert state.get("report") is None


def test_interrupt_and_resume_preserves_facts(tmp_path):
    """G2 gate: interrupt before planning, resume from SQLite checkpoint."""
    run_id = str(uuid.uuid4())
    db = tmp_path / "checkpoint.sqlite3"
    graph = QCGraph(db_path=db, interrupt_before=["plan_next_action"])
    try:
        state = graph.run(run_id, "chloro_C1_NC_013991_2_CHLORO-CDS-03", str(FAULT))
        # stopped right before planning with mandatory facts already in state
        assert state["runtime"]["status"] == "running"
        assert state.get("facts") is not None
        assert state.get("report") is None

        resumed = graph.resume(run_id)
    finally:
        graph.close()
    assert resumed["runtime"]["status"] == "succeeded"
    assert resumed["result"]["qc_decision"] == "block"
    # facts survived the checkpoint round-trip
    assert resumed["facts"] == state["facts"]
    assert resumed["result"]["evidence_validity"] == "valid"


def test_resume_output_equals_uninterrupted_run(tmp_path):
    run_id = str(uuid.uuid4())
    db = tmp_path / "checkpoint.sqlite3"
    interrupted = QCGraph(db_path=db, interrupt_before=["plan_next_action"])
    try:
        interrupted.run(run_id, "chloro_C1_NC_013991_2_CHLORO-NORMAL-00", str(NORMAL))
        resumed = interrupted.resume(run_id)
    finally:
        interrupted.close()
    plain = QCGraph()
    try:
        plain_state = plain.run(run_id, "chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
                                str(NORMAL))
    finally:
        plain.close()
    assert resumed["report"] == plain_state["report"]


def test_resume_after_completion_is_noop(tmp_path):
    run_id = str(uuid.uuid4())
    db = tmp_path / "checkpoint.sqlite3"
    graph = QCGraph(db_path=db)
    try:
        graph.run(run_id, "chloro_C1_NC_013991_2_CHLORO-NORMAL-00", str(NORMAL))
        again = graph.resume(run_id)
    finally:
        graph.close()
    assert again["runtime"]["status"] == "succeeded"
    assert again["report"] is not None  # checkpoint replay, not a second report
