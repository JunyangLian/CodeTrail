"""LLM planner (G4) unit tests - all against a mocked transport, no network."""
from __future__ import annotations

import json
from typing import ClassVar

import httpx
import pytest

from genomeqc_agent.model.gateway import ChatGateway, GatewayError
from genomeqc_agent.model.llm_planner import LLMPlanner, PlannerError
from genomeqc_agent.runtime.graph import QCGraph

OK_PAYLOAD = {
    "choices": [{"message": {"content": json.dumps({
        "kind": "tool", "tool_name": "lookup_feature",
        "arguments": {"feature_id": "CDS_001"}, "reason": "inspect flagged rows"})}}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
}


def _transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    queue = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(queue)

    return httpx.MockTransport(handler)


def _gateway(responses: list[httpx.Response]) -> ChatGateway:
    return ChatGateway(api_key="test-key", api_base="https://mock/v1",
                       model_id="zai-org/GLM-5.3-Flash",
                       transport=_transport(responses))


def test_successful_plan_parsed_and_usage_recorded():
    planner = LLMPlanner(_gateway([httpx.Response(200, json=OK_PAYLOAD)]))
    action = planner.next_action({"statistics": {}}, {"tool_trace": {}})
    assert action.kind == "tool"
    assert action.tool_name == "lookup_feature"
    assert planner.last_usage["total_tokens"] == 120
    assert planner.last_usage["latency_ms"] >= 0


def test_markdown_fences_are_stripped():
    fenced = dict(OK_PAYLOAD)
    fenced["choices"] = [{"message": {"content":
        "```json\n" + json.dumps({"kind": "synthesize", "tool_name": "",
                                  "arguments": {}, "reason": "done"}) + "\n```"}}]
    planner = LLMPlanner(_gateway([httpx.Response(200, json=fenced)]))
    action = planner.next_action({"statistics": {}}, {"tool_trace": {}})
    assert action.kind == "synthesize"


def test_contract_violation_raises_planner_error():
    bad = dict(OK_PAYLOAD)
    # kind pattern is ^(tool|synthesize)$ - "shell" violates the contract
    bad["choices"] = [{"message": {"content": json.dumps({"kind": "shell",
                                                          "tool_name": "run_shell",
                                                          "arguments": {}})}}]
    planner = LLMPlanner(_gateway([httpx.Response(200, json=bad)]))
    with pytest.raises(PlannerError, match="plan contract violation"):
        planner.next_action({"statistics": {}}, {"tool_trace": {}})
    # token usage still recorded for the failed attempt (denominator honesty)
    assert planner.last_usage["total_tokens"] == 120


def test_provider_error_is_wrapped_without_key_leak():
    planner = LLMPlanner(_gateway([httpx.Response(401, text='{"message": "Invalid token"}')]))
    with pytest.raises(PlannerError, match="401"):
        planner.next_action({"statistics": {}}, {"tool_trace": {}})


def test_transport_failure_is_wrapped():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    planner = LLMPlanner(ChatGateway(api_key="k", api_base="https://mock/v1",
                                     model_id="m", transport=httpx.MockTransport(boom)))
    with pytest.raises(PlannerError, match="transport failure"):
        planner.next_action({"statistics": {}}, {"tool_trace": {}})


def test_gateway_requires_credentials():
    with pytest.raises(GatewayError):
        ChatGateway(api_key="", api_base="", model_id="m")


def test_graph_stops_on_planner_failure_without_retry():
    """A failing planner is a terminal recorded outcome (no paid retries)."""

    class FailingPlanner:
        provider = "mock"
        model_id = "mock-planner"
        last_usage: ClassVar[dict] = {"error": "simulated"}

        def next_action(self, facts, context):
            raise PlannerError("simulated planner outage")

    graph = QCGraph(planner=FailingPlanner())
    try:
        state = graph.run(
            "11111111-2222-4333-8444-555555555555",
            "chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
            r"examples\public_packets\chloro_C1_NC_013991_2_CHLORO-NORMAL-00")
    finally:
        graph.close()
    assert state["runtime"]["status"] == "failed"
    assert "planner failed (no paid retry)" in state["runtime"]["last_error"]
    plan_events = [e for e in state["events"] if e["node"] == "plan_next_action"]
    assert len(plan_events) == 0  # the failure is recorded in last_error only
    assert state["runtime"]["model_usage"] == [{"error": "simulated"}]
