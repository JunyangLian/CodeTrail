"""Deterministic policy gate: allowlist, budget and denial recording.

The gate NEVER asks a model what is allowed. A denied plan is recorded and
the planner gets one replan chance with the denial in context (plan §4.1
``record_violation -> replan_once_or_stop``).
"""
from __future__ import annotations

from genomeqc_agent.runtime.state import QCState

MAX_VIOLATIONS = 1  # one replan chance after a denial


def budget_exhausted(state: QCState) -> str | None:
    budgets = state.get("budgets", {})
    if budgets.get("tool_calls", 0) >= budgets.get("max_tool_calls", 0):
        return "tool_calls"
    if budgets.get("model_requests", 0) >= budgets.get("max_model_requests", 0):
        return "model_requests"
    return None


def evaluate(state: QCState, tool_names: list[str]) -> dict:
    """Return {allowed: bool, code, reason} for the planned action."""
    planned = state.get("planned") or {}
    if planned.get("kind") != "tool":
        return {"allowed": True, "code": "", "reason": ""}
    tool_name = planned.get("tool_name", "")
    if tool_name not in tool_names:
        return {"allowed": False, "code": "unknown_tool",
                "reason": f"tool not in allowlist: {tool_name}"}
    exhausted = budget_exhausted(state)
    if exhausted:
        return {"allowed": False, "code": "budget_exhausted",
                "reason": f"budget exhausted before call: {exhausted}"}
    return {"allowed": True, "code": "", "reason": ""}


def retries_left(state: QCState, tool_name: str) -> int:
    budgets = state.get("budgets", {})
    counters = state.get("runtime", {}).get("retry_counters", {})
    limit = int(budgets.get("max_retries_per_tool", 1))
    return max(0, limit - int(counters.get(tool_name, 0)))
