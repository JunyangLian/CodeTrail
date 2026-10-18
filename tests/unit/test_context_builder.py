"""V3.2 P1 ContextBuilder tests: selection, protection, budget, provenance,
position control, and baseline compatibility."""
from __future__ import annotations

import json

from genomeqc_agent.context.builder import (
    CONTEXT_BUILDER_VERSION,
    ContextBuilder,
    builder_enabled,
)

FACTS = {
    "coordinate_violations": [{"pointer": "artifacts/features.tsv:12",
                               "feature_id": "X"}],
    "multipart_structure_issues": [],
    "cds_protein_ids_without_fasta_record": [],
    "fasta_protein_ids_without_cds": [],
    "statistics": {"n_genes": {"pointer": "artifacts/sequence_stats.json:n_genes",
                               "equal": True}},
}


def _big_facts(fill: int = 40_000) -> dict:
    import copy
    facts = copy.deepcopy(FACTS)
    facts["coordinate_violations"][0]["detail"] = "x" * fill
    return facts


def test_protected_blocks_always_present_and_first():
    built = ContextBuilder(token_budget=6_000).build(
        task="decide", packet_id="p1", facts=FACTS,
        tool_trace={"verify_evidence_pointer": [{"pointers": ["a:1"]}]},
        denial=None, evidence={}, user_goal="check CDS")
    names = [b["name"] for b in built["receipt"]["blocks"]]
    assert names[0] == "task"  # critical info first (position control)
    assert "evidence_locators" in names and "denial" not in names
    loc = next(b for b in built["receipt"]["blocks"]
               if b["name"] == "evidence_locators")
    assert loc["protected"] and loc["included"]
    assert "artifacts/sequence_stats.json:n_genes" in built["render"]


def test_denial_is_protected_and_included():
    built = ContextBuilder().build(
        task="decide", packet_id="p1", facts=FACTS, tool_trace={},
        denial={"code": "evidence_unverified", "reason": "verify first"},
        evidence={}, user_goal="")
    assert "evidence_unverified" in built["render"]
    assert any(b["name"] == "denial" and b["protected"]
               for b in built["receipt"]["blocks"])


def test_budget_overflow_reports_partial_and_never_drops_protected():
    built = ContextBuilder(token_budget=900).build(  # tiny budget
        task="decide", packet_id="p1", facts=_big_facts(40_000),
        tool_trace={}, denial=None, evidence={}, user_goal="")
    assert built["receipt"]["completeness"] == "partial"
    # the protected locator survives verbatim even under budget pressure
    assert "artifacts/sequence_stats.json:n_genes" in built["render"]
    # bulk facts were summarised WITH provenance, not silently dropped
    facts_block = next(b for b in built["blocks"] if b["name"] == "facts")
    assert facts_block["compressed"] is True
    assert any(o.get("block") == "facts"
               for o in built["receipt"]["omitted"])


def test_protected_over_budget_reports_without_dropping():
    built = ContextBuilder(token_budget=50).build(  # protected alone > budget
        task="decide", packet_id="p1", facts=FACTS, tool_trace={},
        denial=None, evidence={}, user_goal="")
    assert built["receipt"]["completeness"] == "partial"
    assert any(o.get("severity") == "protected_over_budget"
               for o in built["receipt"]["omitted"])
    # protected content is STILL included verbatim
    assert "artifacts/sequence_stats.json:n_genes" in built["render"]


def test_tool_history_dedup_and_summary_provenance():
    trace = {"lookup_feature": [{"feature_id": f"F{i}"} for i in range(20)]}
    built = ContextBuilder().build(task="d", packet_id="p1", facts=FACTS,
                                   tool_trace=trace, denial=None,
                                   evidence={}, user_goal="")
    block = next(b for b in built["blocks"] if b["name"] == "tool_history")
    parsed = json.loads(block["content"])
    assert len(parsed["recent_calls_newest_last"]) == 6  # detail window
    assert parsed["older_calls_summarised"][0]["tool"] == "lookup_feature"
    assert "NOT raw evidence" in json.dumps(parsed)


def test_provenance_receipt_complete():
    built = ContextBuilder().build(task="d", packet_id="p1", facts=FACTS,
                                   tool_trace={}, denial=None,
                                   evidence={}, user_goal="")
    r = built["receipt"]
    assert r["context_builder_version"] == CONTEXT_BUILDER_VERSION
    assert r["sha256"] and r["tokens_est"] > 0 and r["token_budget"]
    assert r["completeness"] == "full"
    for b in r["blocks"]:
        assert b["sha256"] and "source" in b and "included" in b
    assert "estimate only" in r["estimator"]


def test_deterministic_sha_for_same_inputs():
    kwargs = {"task": "d", "packet_id": "p1", "facts": FACTS,
              "tool_trace": {}, "denial": None, "evidence": {},
              "user_goal": ""}
    a = ContextBuilder().build(**kwargs)["receipt"]["sha256"]
    b = ContextBuilder().build(**kwargs)["receipt"]["sha256"]
    assert a == b


def test_feature_flag_off_by_default():
    assert builder_enabled(None) is False
    assert builder_enabled({}) is False
    assert builder_enabled({"features": {"context_builder_v1": True}}) is True
    assert builder_enabled({"features": {}}) is False
