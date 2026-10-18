"""Deterministic policy gate: allowlist, budget, denial recording, and the
Phase 2 A/B policy guards.

The gate NEVER asks a model what is allowed. A denied plan is recorded and
the planner gets one replan chance with the denial in context (plan §4.1
``record_violation -> replan_once_or_stop``).

Phase 2 policy guards (strategy-carried, OFF by default so the active
strategy's behaviour is byte-identical to the pre-guard runtime):

- ``evidence_enforce_manifest`` (Tool Policy layer): a verify_evidence_pointer
  call citing a pointer whose file part is not in the tool-returned packet
  manifest is denied BEFORE execution (code ``pointer_not_in_manifest``).
  Only pointers that could never resolve are blocked; the denial is recorded,
  so nothing is silently dropped (direction 4 respected).
- ``pre_verify_pointers`` (Evidence Verification layer): a synthesize plan is
  denied while facts-derived pointers exist that were never mechanically
  verified (code ``evidence_unverified``). Closing without verification is
  impossible, not merely discouraged.
- ``unresolved_requires_review`` (Report Composer layer): consumed by the
  composer - unresolved pointers force ``needs_human_review=True``.

All guards are conservative: they can only deny or downgrade, never raise a
verdict or delete evidence.
"""
from __future__ import annotations

from genomeqc_agent.model.scripted_planner import evidence_pointers
from genomeqc_agent.runtime.state import QCState

MAX_VIOLATIONS = 1  # one replan chance after a denial

GUARD_KEYS = ("evidence_enforce_manifest", "pre_verify_pointers",
              "unresolved_requires_review")


def budget_exhausted(state: QCState) -> str | None:
    budgets = state.get("budgets", {})
    if budgets.get("tool_calls", 0) >= budgets.get("max_tool_calls", 0):
        return "tool_calls"
    if budgets.get("model_requests", 0) >= budgets.get("max_model_requests", 0):
        return "model_requests"
    return None


def strategy_guards(state: QCState) -> dict:
    """Policy guards carried by the run's strategy block (empty = baseline)."""
    return (state.get("strategy") or {}).get("policy_guards") or {}


def _deny(code: str, reason: str) -> dict:
    return {"allowed": False, "code": code, "reason": reason}


def evaluate(state: QCState, tool_names: list[str],
             allowed_paths: set[str] | None = None,
             guards: dict | None = None) -> dict:
    """Return {allowed: bool, code, reason} for the planned action."""
    guards = guards if guards is not None else strategy_guards(state)
    planned = state.get("planned") or {}
    if planned.get("kind") == "synthesize":
        if guards.get("pre_verify_pointers"):
            facts = state.get("facts") or {}
            pending = evidence_pointers(facts)
            verified = bool((state.get("tool_trace") or {})
                            .get("verify_evidence_pointer"))
            if pending and not verified:
                return _deny(
                    "evidence_unverified",
                    f"{len(pending)} facts-derived pointer(s) were never "
                    "mechanically verified; verify_evidence_pointer must run "
                    "before synthesize")
        return {"allowed": True, "code": "", "reason": ""}
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
    if (tool_name == "verify_evidence_pointer"
            and guards.get("evidence_enforce_manifest")
            and allowed_paths is not None):
        bad = [p for p in (planned.get("arguments") or {}).get("pointers", [])
               if isinstance(p, str) and p.partition(":")[0] not in allowed_paths]
        if bad:
            return _deny(
                "pointer_not_in_manifest",
                f"{len(bad)} pointer(s) reference files outside the "
                f"tool-returned packet manifest (e.g. {bad[0]}); only "
                "manifest files are citable evidence")
    return {"allowed": True, "code": "", "reason": ""}


def retries_left(state: QCState, tool_name: str) -> int:
    budgets = state.get("budgets", {})
    counters = state.get("runtime", {}).get("retry_counters", {})
    limit = int(budgets.get("max_retries_per_tool", 1))
    return max(0, limit - int(counters.get(tool_name, 0)))
