"""Scripted planner: deterministic next-action decisions, zero network.

Extends FakeModel with the G2 planner contract. The decision table mirrors
the smoke pipeline branching but emits single structured actions that the
policy gate evaluates, exactly as a real GLM/DeepSeek planner would have to
(sans the freedom - which arrives only with authorized model runs in G4).
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from genomeqc_agent.model.fake_model import FakeModel, ModelCallRecord

FACT_KEYS = ("coordinate_violations", "multipart_structure_issues",
             "cds_protein_ids_without_fasta_record", "fasta_protein_ids_without_cds",
             "statistics")


class PlannedAction(BaseModel):
    kind: str = Field(pattern="^(tool|synthesize)$")
    tool_name: str = ""
    arguments: dict = Field(default_factory=dict)
    reason: str = ""

    @field_validator("tool_name", mode="before")
    @classmethod
    def _null_tool_name_is_empty(cls, value):
        """Real models emit null for tool_name on synthesize - that is the
        contract's own meaning (no tool), so normalize instead of failing."""
        return "" if value is None else value


def evidence_pointers(facts: dict) -> list[str]:
    """Collect artifact-file pointers from facts (tool fields are NOT artifacts)."""
    pointers: list[str] = []
    pointers += [v["pointer"] for v in facts.get("coordinate_violations") or []]
    pointers += [v["pointer"] for v in facts.get("multipart_structure_issues") or []]
    pointers += [p for item in facts.get("cds_protein_ids_without_fasta_record") or []
                 for p in item.get("pointers", [])]
    pointers += [item["pointer"] for item in facts.get("fasta_protein_ids_without_cds") or []]
    pointers += [pair["pointer"] for pair in (facts.get("statistics") or {}).values()]
    return pointers


class ScriptedPlanner(FakeModel):
    """Deterministic planner: inspect -> summary -> (lookup if flagged) ->
    verify pointers -> synthesize. Same decision logic the G4 prompt must
    reproduce before any real model is authorized."""

    def next_action(self, facts: dict | None, context: dict) -> PlannedAction:
        self.requests.append(ModelCallRecord(
            purpose="plan_next_action", model_id=self.model_id, provider=self.provider,
            prompt_chars=len(repr({"facts": facts, "context": context})),
        ))
        if facts is None or any(key not in facts for key in FACT_KEYS):
            return PlannedAction(kind="synthesize",
                                 reason="facts unavailable or incomplete -> no QC claim")
        issues = facts.get("multipart_structure_issues") or []
        prior_calls = (context.get("tool_trace") or {}).get("lookup_feature") or []
        looked_up = {call.get("feature_id") for call in prior_calls
                     if isinstance(call, dict)}
        pending = [i for i in issues if i.get("feature_id") not in looked_up]
        if pending:
            feature_id = pending[0]["feature_id"]
            return PlannedAction(
                kind="tool", tool_name="lookup_feature",
                arguments={"feature_id": feature_id},
                reason=f"multipart issue flagged for {feature_id}; inspect exact rows")
        trace = context.get("tool_trace") or {}
        if "verify_evidence_pointer" not in trace:
            pointers = evidence_pointers(facts)[:50]
            if pointers:
                return PlannedAction(
                    kind="tool", tool_name="verify_evidence_pointer",
                    arguments={"pointers": pointers},
                    reason="every pointer the report will cite must resolve "
                           "(pointer valid != evidence sufficient; this checks the former)")
        return PlannedAction(kind="synthesize",
                             reason="mandatory checks + evidence verification complete")
