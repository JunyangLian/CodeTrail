"""P1.1 bounded evidence self-refinement tests (offline, zero API).

Scenarios required by the P2 checklist: correction success, correction
failure, rejected correction (re-verify does not resolve), and the
feature-flag default-off guarantee. Hard rules under test: <=2 rounds,
before/after trace kept, no evidence deletion, partial never forced valid.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from genomeqc_agent.model.scripted_planner import PlannedAction, evidence_pointers
from genomeqc_agent.runtime.graph import QCGraph
from genomeqc_agent.runtime.refinement import (
    apply_verify_result,
    classify_unresolved,
    plan_corrections,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "public_packets"
NORMAL = EXAMPLES / "chloro_C1_NC_013991_2_CHLORO-NORMAL-00"
NORMAL_ID = "chloro_C1_NC_013991_2_CHLORO-NORMAL-00"

FEATURE = {"features": {"self_refinement": True}}


class PointerStub:
    """Scripted planner that verifies one configurable hallucinated pointer
    alongside the real facts-derived pointers."""

    provider = "stub"
    model_id = "stub-planner"

    def __init__(self, hallucinated: list[str]):
        self.hallucinated = hallucinated

    def next_action(self, facts: dict | None, context: dict) -> PlannedAction:
        trace = context.get("tool_trace") or {}
        if "verify_evidence_pointer" not in trace:
            pointers = self.hallucinated + evidence_pointers(facts or {})[:49]
            return PlannedAction(kind="tool", tool_name="verify_evidence_pointer",
                                 arguments={"pointers": pointers},
                                 reason="verify (hallucinated file injected)")
        return PlannedAction(kind="synthesize", reason="done")


def _run(packet_dir: Path, packet_id: str, planner, strategy: dict) -> dict:
    graph = QCGraph(planner=planner)
    try:
        return graph.run(str(uuid.uuid4()), packet_id, str(packet_dir),
                         strategy=strategy)
    finally:
        graph.close()


# -- unit: mechanical diagnosis ------------------------------------------------

def test_unique_stem_match_proposes_correction():
    manifest = {"artifacts/proteins.faa", "artifacts/genome.fa"}
    diagnosis = classify_unresolved("proteins.faa.txt:>", manifest)
    assert diagnosis["reason"] == "file_not_in_manifest"
    assert diagnosis["correction"] == "artifacts/proteins.faa:>"


def test_ambiguous_stem_is_not_correctable():
    manifest = {"a/data.json", "b/data.json"}
    candidates, uncorrectable = plan_corrections(["data.json:k"], manifest)
    assert candidates == []
    assert uncorrectable[0]["reason"] == "file_not_in_manifest"


def test_locator_miss_is_never_corrected():
    # file exists in the manifest: the locator must never be invented
    diagnosis = classify_unresolved("artifacts/genome.fa:not-a-key",
                                    {"artifacts/genome.fa"})
    assert diagnosis == {"reason": "locator_miss", "correction": None}


def test_apply_verify_result_only_moves_verified_pointers():
    evidence = {"pointers": [{"pointer": "ghost.fa:k", "resolved": False}],
                "unresolved": ["ghost.fa:k"]}
    merged = apply_verify_result(evidence, accepted=[], rejected=[])
    assert merged["unresolved"] == ["ghost.fa:k"]  # nothing removed without proof


# -- graph: end-to-end scenarios ------------------------------------------------

def test_correction_success_upgrades_partial_to_valid():
    state = _run(NORMAL, NORMAL_ID, PointerStub(["proteins.faa.txt:>"]),
                 {"prompt_version": "promptv1", **FEATURE})
    assert state["runtime"]["status"] == "succeeded"
    assert state["result"]["evidence_validity"] == "valid"  # re-verified fix
    record = state["refinement"]
    assert record["before_unresolved"] == ["proteins.faa.txt:>"]
    assert record["after_unresolved"] == []
    assert record["corrected"] == [{"from": "proteins.faa.txt:>""",
                                    "to": "artifacts/proteins.faa:>"}]
    assert any(e["node"] == "self_refinement" for e in state["events"])
    assert len(record["rounds"]) <= 2  # bounded
    # the correction is itself mechanically verified in the tool trace
    assert any(call.get("pointers") == ["artifacts/proteins.faa:>"]
               for call in state["tool_trace"]["verify_evidence_pointer"][1:])


def test_uncorrectable_stays_partial_with_review_flag():
    state = _run(NORMAL, NORMAL_ID, PointerStub(["ghost.fa:KEY"]),
                 {"prompt_version": "promptv1",
                  "features": {"self_refinement": True},
                  "policy_guards": {"unresolved_requires_review": True}})
    assert state["result"]["evidence_validity"] == "partial"  # never forced valid
    assert state["report"]["needs_human_review"] is True
    record = state["refinement"]
    assert record["before_unresolved"] == record["after_unresolved"]
    assert record["corrected"] == []  # nothing fixed
    assert record["uncorrectable"][0]["pointer"] == "ghost.fa:KEY"
    # findings/evidence untouched: no evidence was deleted
    assert state["report"]["findings"]


def test_rejected_correction_never_fakes_resolution():
    """Stem match exists but the locator is invented: the re-verify must
    reject the correction and the report must stay partial."""
    state = _run(NORMAL, NORMAL_ID, PointerStub(["proteins.fa:INVENTED-LOCATOR"]),
                 {"prompt_version": "promptv1", **FEATURE})
    assert state["result"]["evidence_validity"] == "partial"
    record = state["refinement"]
    assert record["corrected"] == []
    assert any(u["reason"] == "correction_did_not_resolve"
               for u in record["uncorrectable"])
    assert record["after_unresolved"] == ["proteins.fa:INVENTED-LOCATOR"]


def test_refinement_off_by_default_leaves_no_trace():
    state = _run(NORMAL, NORMAL_ID, PointerStub(["ghost.fa:KEY"]),
                 {"prompt_version": "promptv1"})
    assert state["result"]["evidence_validity"] == "partial"
    assert "refinement" not in state  # feature flag absent -> engine never ran
