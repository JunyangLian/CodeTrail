"""LLM planner (G4): real model chooses the next action under the same
PlannedAction contract the policy gate enforces.

Failure semantics (master plan §6: paid calls are never auto-retried):
any gateway/parse failure raises PlannerError once and the graph stops the
run - a malformed plan is a recorded outcome, not something to paper over
with the scripted planner.
"""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from genomeqc_agent.model.gateway import ChatGateway, GatewayError
from genomeqc_agent.model.scripted_planner import FACT_KEYS, PlannedAction

SYSTEM_PROMPT_VERSION = "promptv1"

SYSTEM_PROMPT = """You are the planner of a read-only organelle genome QC agent.
The mandatory deterministic checks (inspect_packet, organelle_summary) have
already run; their facts are given to you. Decide the ONE next action.

Available tools (read-only, whitelisted):
- lookup_feature: {"feature_id": "..."} - fetch exact feature rows
- verify_evidence_pointer: {"pointers": ["path:locator", ...]} - check that
  every evidence pointer the final report will cite resolves (max 50)

Rules:
- Facts are produced by tools; you only choose and explain the next step.
- If flagged features need row-level inspection, plan lookup_feature.
- Before finishing, plan verify_evidence_pointer covering the pointers the
  report will cite. A pointer being locatable does NOT mean the evidence is
  sufficient - but resolution must be verified mechanically.
- Finish with kind="synthesize" when verification is done.

Respond with STRICT JSON only, no prose, no markdown fences:
{"kind": "tool"|"synthesize", "tool_name": "...", "arguments": {}, "reason": "..."}"""


class PlannerError(RuntimeError):
    """One planning attempt failed (transport, auth, or contract)."""


class LLMPlanner:
    provider = "siliconflow"
    prompt_version = SYSTEM_PROMPT_VERSION  # E1: strategy candidates version this

    def __init__(self, gateway: ChatGateway, max_tokens: int = 4096):
        self.gateway = gateway
        self.model_id = gateway.model_id
        self.max_tokens = max_tokens
        self.last_usage: dict | None = None  # read by the graph right after a call

    def next_action(self, facts: dict | None, context: dict) -> PlannedAction:
        user = json.dumps({
            "facts": facts,
            "tool_trace": context.get("tool_trace") or {},
            "previous_denial": context.get("denial"),
            "notes": ("facts keys expected: " + ", ".join(FACT_KEYS)
                      if facts and any(k not in (facts or {}) for k in FACT_KEYS) else ""),
        }, ensure_ascii=False)
        try:
            call = self.gateway.chat_json(SYSTEM_PROMPT, user,
                                          max_tokens=self.max_tokens)
        except GatewayError as error:
            self.last_usage = {"error": str(error)}
            raise PlannerError(f"gateway: {error}") from error
        self.last_usage = {
            "latency_ms": call["latency_ms"],
            "prompt_tokens": call["usage"].get("prompt_tokens"),
            "completion_tokens": call["usage"].get("completion_tokens"),
            "total_tokens": call["usage"].get("total_tokens"),
            "reasoning_chars": call.get("reasoning_chars"),
        }
        try:
            payload = json.loads(_strip_fences(call["content"]))
            return PlannedAction.model_validate(payload)
        except (ValueError, ValidationError) as error:
            raise PlannerError(f"plan contract violation: {error}") from error


def _strip_fences(text: str) -> str:
    """Tolerate markdown fences some models add despite instructions."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text
