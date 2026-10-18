"""V3.2 P1: ContextBuilder - model-external context organisation.

Task -> State Reader -> Context Selector -> Context Budgeter -> LLMPlanner.

Design rules (user directive):
- PROTECTED blocks are never silently compressed or dropped: current task
  requirements, file manifest / real paths, evidence locators, tool-schema
  key parameters (denial), unresolved errors, execution constraints.
- If protected content exceeds the token budget the builder reports
  ``completeness: partial`` explicitly - it never pretends the context is
  complete, and it never deletes protected content to fit.
- Non-protected bulk (facts detail, old tool calls) may be summarised with
  full provenance; a summary is NEVER presented as raw evidence.
- Critical information is placed FIRST (task/denial), evidence in the
  middle, bulk history LAST - position control for long contexts.
- Every build emits a provenance receipt: version, sha256, per-block
  source/included/omitted/compressed flags, estimated tokens (est vs the
  provider's actual usage are recorded separately).
- V3.1 baseline path is untouched: the builder is opt-in per run
  (strategy.features.context_builder_v1) and never mutates prompt v1/v2.
"""
from __future__ import annotations

import hashlib
import json

CONTEXT_BUILDER_VERSION = "contextv1"
DEFAULT_TOKEN_BUDGET = 6_000          # input tokens for the USER message
CHARS_PER_TOKEN = 4                   # estimator; actual usage recorded apart
MAX_TOOL_CALLS_DETAILED = 6           # most recent calls kept verbatim


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _est(text: str) -> int:
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def _dedup_tool_trace(tool_trace: dict) -> list[dict]:
    """Flatten {tool: [args...]} into a call list (newest last), deduped on
    (tool, canonical args) with a repeat counter."""
    calls: list[dict] = []
    seen: dict[str, int] = {}
    for tool, arg_list in (tool_trace or {}).items():
        for args in arg_list or []:
            key = tool + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False)
            if key in seen:
                calls[seen[key]]["repeats"] += 1
                continue
            seen[key] = len(calls)
            calls.append({"tool": tool, "arguments": args, "repeats": 1})
    return calls


class ContextBuilder:
    def __init__(self, token_budget: int = DEFAULT_TOKEN_BUDGET):
        self.token_budget = token_budget

    def build(self, *, task: str, packet_id: str, facts: dict | None,
              tool_trace: dict | None, denial: dict | None,
              evidence: dict | None, user_goal: str = "") -> dict:
        blocks: list[dict] = []

        def add(name: str, source: str, content: str, protected: bool):
            blocks.append({
                "name": name, "source": source, "protected": protected,
                "chars": len(content), "tokens_est": _est(content),
                "sha256": _sha(content), "content": content, "included": True,
                "compressed": False, "omitted_reason": None,
            })

        # -- protected block 1: task + goal (placed FIRST - position control)
        add("task", "state.input + user_goal",
            f"TASK: QC a public organelle genome packet `{packet_id}`.\n"
            f"USER GOAL: {user_goal or '(none)'}\n"
            "CONSTRAINTS: read-only tools; decide the ONE next action; "
            "finish with kind=synthesize when verification is done.",
            protected=True)

        # -- protected block 2: denial (policy feedback)
        if denial:
            add("denial", "runtime.denial (policy gate)",
                json.dumps(denial, ensure_ascii=False, sort_keys=True),
                protected=True)

        # -- protected block 3: manifest + evidence locators + unresolved
        manifest_lines: list[str] = []
        unresolved = list((evidence or {}).get("unresolved") or [])
        if facts:
            for v in facts.get("coordinate_violations") or []:
                manifest_lines.append(v["pointer"])
            for v in facts.get("multipart_structure_issues") or []:
                manifest_lines.append(v["pointer"])
            for item in facts.get("cds_protein_ids_without_fasta_record") or []:
                manifest_lines.extend(item.get("pointers", []))
            for item in facts.get("fasta_protein_ids_without_cds") or []:
                if item.get("pointer"):
                    manifest_lines.append(item["pointer"])
            for pair in (facts.get("statistics") or {}).values():
                manifest_lines.append(pair["pointer"])
        evidence_src = "facts pointers + state.evidence.unresolved"
        evidence_lines = sorted(set(manifest_lines)) + [
            f"UNRESOLVED: {p}" for p in unresolved]
        add("evidence_locators", evidence_src,
            "\n".join(evidence_lines) if evidence_lines
            else "(no facts-derived pointers yet)", protected=True)

        # -- bulk block 4: facts detail (may be summarised under budget)
        add("facts", "organelle_summary result",
            json.dumps(facts, ensure_ascii=False, sort_keys=True)
            if facts else "(facts unavailable)", protected=False)

        # -- bulk block 5: tool history (dedup, newest-first detail window)
        calls = _dedup_tool_trace(tool_trace)
        detailed = calls[-MAX_TOOL_CALLS_DETAILED:]
        older = calls[:-MAX_TOOL_CALLS_DETAILED] if len(calls) > \
            MAX_TOOL_CALLS_DETAILED else []
        history = json.dumps(
            {"recent_calls_newest_last": detailed,
             "older_calls_summarised": [
                 {"tool": c["tool"], "repeats": c["repeats"],
                  "note": "arguments omitted (provenance: tool_trace); a "
                          "summary is NOT raw evidence"} for c in older]},
            ensure_ascii=False, sort_keys=True)
        add("tool_history", "state.tool_trace (deduped)", history,
            protected=False)

        # -- budgeting: protected always included; bulk trimmed if needed
        protected_est = sum(b["tokens_est"] for b in blocks if b["protected"])
        bulk = [b for b in blocks if not b["protected"]]
        completeness = "full"
        omitted: list[dict] = []
        budget_left = self.token_budget - protected_est
        if protected_est > self.token_budget:
            # never drop protected content - report incomplete honestly
            completeness = "partial"
            omitted.append({
                "severity": "protected_over_budget",
                "detail": f"protected blocks need {protected_est} est tokens "
                          f"> budget {self.token_budget}; all protected "
                          "content is STILL included verbatim",
            })
        for b in bulk:
            if b["tokens_est"] > budget_left:
                summary = (f"[SUMMARISED] {b['name']}: {b['chars']} chars "
                           f"(~{b['tokens_est']} est tokens) exceeded the "
                           f"remaining context budget; content omitted with "
                           f"provenance sha256={b['sha256']}. A summary is "
                           "NOT raw evidence.")
                omitted.append({"block": b["name"],
                                "omitted_sha256": b["sha256"],
                                "omitted_tokens_est": b["tokens_est"]})
                b["content"] = summary
                b["compressed"] = True
                b["tokens_est"] = _est(summary)
                b["chars"] = len(summary)
                completeness = "partial" if completeness != "partial" \
                    else completeness
                budget_left -= b["tokens_est"]
            else:
                budget_left -= b["tokens_est"]

        render = "\n\n".join(b["content"] for b in blocks)
        receipt = {
            "context_builder_version": CONTEXT_BUILDER_VERSION,
            "sha256": _sha(render),
            "tokens_est": sum(b["tokens_est"] for b in blocks),
            "token_budget": self.token_budget,
            "completeness": completeness,
            "blocks": [{k: v for k, v in b.items() if k != "content"}
                       for b in blocks],
            "omitted": omitted,
            "estimator": f"chars/{CHARS_PER_TOKEN} (estimate only; actual "
                         "provider usage recorded separately in model_usage)",
        }
        return {"render": render, "receipt": receipt, "blocks": blocks}


def builder_enabled(strategy: dict | None) -> bool:
    return bool((strategy or {}).get("features", {})
                .get("context_builder_v1"))
