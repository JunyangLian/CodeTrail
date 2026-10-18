"""Offline FakeModel: scripted planner, zero network, records every call.

G1 has no LLM in the loop; this provider stands in for the G2+ LangGraph
planner so the pipeline and budget accounting are exercised end to end.
"""
from __future__ import annotations

from pydantic import BaseModel


class PlannedToolCall(BaseModel):
    tool_name: str
    arguments: dict = {}


class ModelCallRecord(BaseModel):
    purpose: str
    model_id: str
    provider: str
    prompt_chars: int


class FakeModel:
    provider = "fake"
    model_id = "fake-planner-v0"
    prompt_version = "scripted-v1"

    def __init__(self) -> None:
        self.requests: list[ModelCallRecord] = []

    @property
    def request_count(self) -> int:
        return len(self.requests)

    def plan(self, purpose: str, packet_summary: dict) -> list[PlannedToolCall]:
        """Scripted check plan: intake facts first, then deep organelle facts.

        Deterministic on purpose: the G1 smoke asserts that the plan executes
        and that every finding traces back to these tool calls.
        """
        self.requests.append(ModelCallRecord(
            purpose=purpose, model_id=self.model_id, provider=self.provider,
            prompt_chars=len(repr(packet_summary)),
        ))
        return [
            PlannedToolCall(tool_name="inspect_packet", arguments={}),
            PlannedToolCall(tool_name="organelle_summary", arguments={}),
        ]
