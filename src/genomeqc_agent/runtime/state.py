"""Runtime state contract (plan §4.2, JSON-serializable for the checkpointer).

Only primitives/dicts/lists travel through state; packet bytes and tool
snapshots never do. Resume re-opens the read-only sandbox from packet_dir,
so recovery is idempotent and side-effect free.
"""
from __future__ import annotations

from typing import TypedDict

# Terminal runtime.status values (plan §4.2).
TERMINAL_STATUSES = ("succeeded", "needs_human_review", "insufficient_evidence",
                     "failed", "cancelled", "budget_exhausted")


class QCState(TypedDict, total=False):
    run_id: str
    # input -----------------------------------------------------------------
    input: dict          # {packet_id, packet_dir, domain, user_goal, input_sha256}
    # runtime ---------------------------------------------------------------
    runtime: dict        # {status, step, model_id, violations, last_error, denial}
    budgets: dict        # {max_model_requests, max_tool_calls, max_retries_per_tool,
                         #  model_requests, tool_calls}
    # evidence & planning ---------------------------------------------------
    facts: dict | None   # organelle_summary facts (tool-produced only)
    evidence: dict       # verify_evidence_pointer report
    tool_trace: dict     # {tool_name: [arguments...]} for planner context
    planned: dict | None # {kind: tool|synthesize, tool_name, arguments, reason}
    pending_call: dict | None
    last_result: dict | None  # ToolResult dump of the most recent execution
    # strategy (Phase 2 A/B): {prompt_version, prompt_sha256, policy_guards{}}
    # absent/empty = baseline active strategy; guards all default OFF
    strategy: dict
    # output ----------------------------------------------------------------
    report: dict | None
    refinement: dict      # P1.1 self-refinement before/after trace (audit)
    events: list[dict]   # [{step, node, summary}] - append-only audit trail
    result: dict | None
