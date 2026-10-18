"""M2 Strategy Proposer + M3 Offline Evolution Controller + M4 E2 pool.

Evolution state machine (each transition writes a receipt):
    proposed -> validated -> shadow_tested -> accepted|rejected
    accepted -> approved (HUMAN APPROVAL REQUIRED - hard gate)
    approved -> published (active strategy updated, versioned)

Phase 1 runs entirely as ``offline_simulation``: quality gates are fed
mock/historical metrics, no model API is called, and a failed gate
auto-rejects the candidate without touching the active production strategy.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from genomeqc_agent.evolution.prompts import PromptRegistry

SIMULATION_MARK = "offline_simulation"

# M2: the ONLY config fields a proposer may touch (whitelist).
PARAMETER_WHITELIST = {
    "max_tokens": [4096, 8192],
    "gateway_timeout_s": [60, 120, 240],
    "verify_pointer_cap": [50, 100],
}

# M3: state machine.
VALID_TRANSITIONS = {
    "proposed": ["validated", "rejected"],
    "validated": ["shadow_tested", "rejected"],
    "shadow_tested": ["accepted", "rejected"],
    "accepted": ["approved"],
    "approved": ["published"],
    "rejected": [],
    "published": [],
}
TERMINAL = {"rejected", "published"}

# Phase 1.5 (P1): candidates for the real invalid_evidence_pointer failure
# may only climb to `validated` - real shadow/A-B evidence needs Phase 2.
BEHAVIOR_POLICY_MAX_STATUS = "validated"

# P1 allowed candidate directions (user directive), enforced verbatim.
BEHAVIOR_POLICY_DIRECTIONS = [
    "evidence pointers must reference files from the actual tool-returned manifest",
    "locators must be verified before citation",
    "insufficient evidence must be flagged partial/needs_review",
    "necessary evidence must never be dropped to raise pass rates",
]



def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# rank order used to enforce per-candidate status caps (Phase 1.5 P1)
_STATUS_RANK = {"proposed": 0, "validated": 1, "shadow_tested": 2, "accepted": 3,
                "rejected": 3, "approved": 4, "published": 5}


def _rank(status: str) -> int:
    return _STATUS_RANK.get(status, 0)


class EvolutionError(RuntimeError):
    """State machine violation (e.g. publishing without human approval)."""


# -- M2: proposer -------------------------------------------------------------

class StrategyProposer:
    """Generates candidates ONLY inside the whitelist; refuses when the
    failure pool carries no actionable root cause (M2.6)."""

    def __init__(self, active_config: dict, base_prompt_version: str = "promptv1"):
        self.active_config = dict(active_config)
        self.base_prompt_version = base_prompt_version

    def propose(self, failure_entries: list[dict]) -> dict:
        candidates: list[dict] = []
        refusals: list[str] = []
        by_cat = {}
        for entry in failure_entries:
            by_cat.setdefault(entry["category"], []).append(entry)

        truncations = by_cat.get("infrastructure_error", [])
        truncation_like = [e for e in truncations
                           if "truncat" in (e.get("mechanism") or "")
                           or "max_tokens too small" in (e.get("mechanism") or "")]
        if truncation_like and self.active_config.get("max_tokens") == 4096:
            candidates.append(self._candidate(
                {"max_tokens": 8192},
                root_cause=f"{len(truncation_like)} planning call(s) truncated at 4096 "
                           f"(e.g. {truncation_like[0]['case_id']})",
                failure_refs=[e.get("entry_id") or e["case_id"]
                              for e in truncation_like]))
        timeouts = [e for e in truncations if "timeout" in (e.get("mechanism") or "")]
        if timeouts and self.active_config.get("gateway_timeout_s", 120) < 240:
            candidates.append(self._candidate(
                {"gateway_timeout_s": 240},
                root_cause=f"{len(timeouts)} read timeout(s) at the current timeout",
                failure_refs=[e.get("entry_id") or e["case_id"] for e in timeouts]))

        hallucinations = by_cat.get("model_planning_error", [])
        if hallucinations:
            refusals.append(
                f"{len(hallucinations)} model_planning_error case(s): no whitelisted "
                "parameter remediates hallucinated pointers; a prompt change requires "
                "a human-authored prompt candidate (outside proposer scope)")

        if not candidates:
            return {"proposed": False,
                    "reason": ("no candidate: " + "; ".join(refusals))
                    if refusals else
                    "no candidate: failure pool has no whitelisted-parameter root cause",
                    "candidates": [], "refusals": refusals}
        return {"proposed": True, "candidates": candidates,
                "refusals": refusals}

    def _candidate(self, config_delta: dict, root_cause: str,
                   failure_refs: list[str]) -> dict:
        return {
            "config_delta": config_delta,
            "resulting_config": {**self.active_config, **config_delta},
            "root_cause": root_cause,
            "failure_refs": failure_refs,
            "rollback_ref": json.dumps(self.active_config, sort_keys=True),
            "proposer": "StrategyProposer",
            "simulation": SIMULATION_MARK,
        }


# -- M3: controller -----------------------------------------------------------

class EvolutionController:
    """Drives the candidate state machine with receipts. Human approval is a
    hard gate: ``approve`` must be called with human=True to move
    accepted -> approved; without it the candidate stays stuck (M3.3)."""

    def __init__(self, receipts_dir: Path, active_config: dict | None = None):
        self.receipts_dir = Path(receipts_dir)
        self.receipts_dir.mkdir(parents=True, exist_ok=True)
        self.active_config = dict(active_config or {})
        self.state: dict = {}
        self.simulation = SIMULATION_MARK

    def propose(self, candidate_id: str, proposal: dict) -> dict:
        candidate = {
            "candidate_id": candidate_id,
            "status": "proposed",
            "proposal": proposal,
            "history": [{"at": _now(), "transition": "proposed"}],
            "simulation": self.simulation,
        }
        self.state[candidate_id] = candidate
        self._receipt(candidate_id, "proposed", {"config_delta": proposal.get("config_delta")})
        return candidate

    def quality_gate(self, baseline: dict, candidate: dict) -> tuple[bool, str]:
        """Quality threshold (M3.2): pass-rate and pointer-resolution must not
        drop, zero critical false passes, denominators complete."""
        for metric in ("judgment_pass_rate", "pointer_resolution_rate"):
            base = baseline.get(metric)
            cand = candidate.get(metric)
            if base is None or cand is None:
                return False, f"missing metric: {metric}"
            if cand < base:
                return False, f"{metric} regressed: {base} -> {cand}"
        if candidate.get("critical_false_pass", 0) > 0:
            return False, "critical false pass present"
        for side in (baseline, candidate):
            if not side.get("denominator"):
                return False, "incomplete denominator"
        return True, "all thresholds hold"

    def transition(self, candidate_id: str, target: str,
                   metrics: dict | None = None,
                   baseline: dict | None = None,
                   human_approval: bool = False,
                   shadow_result: dict | None = None) -> dict:
        candidate = self.state.get(candidate_id)
        if candidate is None:
            raise EvolutionError(f"unknown candidate: {candidate_id}")
        current = candidate["status"]
        cap = candidate.get("status_cap")
        if cap and VALID_TRANSITIONS[current] and _rank(target) > _rank(cap):
            raise EvolutionError(
                f"candidate {candidate_id} is capped at '{cap}': reaching '{target}' "
                f"requires real A/B evidence (Phase 2 authorisation)")
        if target not in VALID_TRANSITIONS[current]:
            raise EvolutionError(f"illegal transition {current} -> {target}")

        if target == "validated":
            ok, reason = self.quality_gate(baseline or {}, metrics or {})
            if not ok:
                return self._reject(candidate_id, candidate, reason)
            candidate["gate"] = {"passed": True, "reason": reason,
                                 "metrics": metrics, "simulation": self.simulation}
        if target == "shadow_tested":
            candidate["shadow"] = {"result": shadow_result or {},
                                   "simulation": self.simulation}
        if target == "approved" and not human_approval:
            # M3.3: human approval genuinely blocks publishing
            raise EvolutionError(
                "human approval required: accepted candidates cannot be "
                "approved or published without human_approval=True")
        if target == "approved":
            # Phase 2 P2: a SIMULATED approval receipt can never authorize
            # publishing - the receipt must exist, say approve, and be marked
            # real. This is a state assertion, not an honour-system flag.
            approval = candidate.get("approval") or {}
            if not (approval.get("decision") == "approve"
                    and approval.get("real") is True):
                raise EvolutionError(
                    "real human approval receipt required: a simulated "
                    "approval cannot authorize publishing (record one via "
                    "record_approval(..., real=True))")
        if target == "rejected":
            reason = (metrics or {}).get("reason") or candidate.get("gate", {}).get("reason")
            candidate["rejection_reason"] = reason

        candidate["status"] = target
        candidate["history"].append({"at": _now(), "transition": target,
                                     "human_approval": human_approval,
                                     "simulation": self.simulation})
        self._receipt(candidate_id, target,
                      {"metrics": metrics, "human_approval": human_approval})
        if target == "published":
            self.active_config.update(candidate["proposal"]["config_delta"])
            candidate["published_config"] = dict(self.active_config)
            self._receipt(candidate_id, "published",
                          {"active_config": self.active_config})
        return candidate

    def _reject(self, candidate_id: str, candidate: dict, reason: str) -> dict:
        candidate["status"] = "rejected"
        candidate["rejection_reason"] = reason
        candidate["history"].append({"at": _now(), "transition": "rejected",
                                     "reason": reason,
                                     "simulation": self.simulation})
        self._receipt(candidate_id, "rejected", {"reason": reason})
        return candidate

    # -- Phase 1.5 (P1): restricted behaviour-policy candidates -------------

    def propose_behavior_policy(self, candidate_id: str, failure_entries: list[dict],
                                prompt_registry=None, base_prompt: str = "promptv1",
                                candidate_prompt: str = "promptv2") -> dict:
        """Restricted candidate for the real invalid_evidence_pointer failure.

        The candidate is a behaviour policy: a config delta (runtime guards)
        plus a prompt diff (pointer hygiene constraints). It may only reach
        ``validated`` status; shadow/A-B needs Phase 2 authorisation. Python
        source, the Oracle, the Scorer and frozen data are untouched by
        construction - the delta carries no code path.
        """
        hallucinations = [e for e in failure_entries
                          if e.get("category") == "model_planning_error"]
        if not hallucinations:
            raise EvolutionError(
                "no model_planning_error failure in pool: refusing to propose "
                "a pointer-guard candidate without real failure evidence")
        evidence_refs = []
        for entry in hallucinations:
            unresolved = (entry.get("error_evidence") or {}).get("unresolved_pointers")
            if unresolved:
                evidence_refs.append({"case_id": entry["case_id"],
                                      "unresolved_pointers": unresolved})
        prompt_diff = None
        candidate_prompt_sha = None
        if prompt_registry is not None:
            prompt_diff = prompt_registry.diff(base_prompt, candidate_prompt)
            candidate_prompt_sha = prompt_registry.get(candidate_prompt)["sha256"]
        proposal = {
            "kind": "behavior_policy",
            "config_delta": {
                "evidence_enforce_manifest": True,   # direction 1
                "pre_verify_pointers": True,         # direction 2
                "unresolved_requires_review": True,  # direction 3
            },
            "prompt_version": candidate_prompt,
            "prompt_sha256": candidate_prompt_sha,
            "prompt_diff": prompt_diff,
            "allowed_directions": list(BEHAVIOR_POLICY_DIRECTIONS),
            "root_cause": (f"{len(hallucinations)} real invalid_evidence_pointer "
                           f"failure(s): planner cited pointers outside the packet "
                           f"manifest or with invented locators"),
            "failure_refs": evidence_refs or [e.get("entry_id") or e.get("case_id")
                                              for e in hallucinations],
            "rollback_ref": json.dumps({"prompt_version": base_prompt,
                                        "config_delta": {}}, sort_keys=True),
            "no_evidence_dropping_rule": BEHAVIOR_POLICY_DIRECTIONS[3],
            "max_status": BEHAVIOR_POLICY_MAX_STATUS,
            "simulation": self.simulation,
        }
        candidate = self.propose(candidate_id, proposal)
        candidate["status_cap"] = BEHAVIOR_POLICY_MAX_STATUS
        return candidate

    # -- Phase 1.5 (P2): governed human approvals ---------------------------

    def record_approval(self, candidate_id: str, approver: str, decision: str,
                        candidate_version: int = 1,
                        real: bool = False) -> dict:
        """Record a human approval decision (approve|reject) with identity.

        ``real=True`` marks an actual human decision by a named approver;
        simulated drivers must leave it False. Only a real approval receipt
        can unlock the approved -> published transition (Phase 2 P2). A
        rejection moves the candidate to the rejected terminal state and
        leaves the active strategy untouched.
        """
        candidate = self.state.get(candidate_id)
        if candidate is None:
            raise EvolutionError(f"unknown candidate: {candidate_id}")
        if decision not in ("approve", "reject"):
            raise EvolutionError(f"illegal decision: {decision}")
        receipt = {
            "candidate_id": candidate_id,
            "candidate_version": candidate_version,
            "approver": approver,
            "decision": decision,
            "real": bool(real),
            "active_config_sha256": PromptRegistry.sha256(
                json.dumps(self.active_config, sort_keys=True)),
            "at": _now(),
            "simulation": self.simulation,
        }
        target = self.receipts_dir / candidate_id
        target.mkdir(parents=True, exist_ok=True)
        (target / f"approval_{decision}_{candidate_version}.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        if decision == "reject":
            candidate = self._reject(candidate_id, candidate,
                                     f"human rejection by {approver}")
        candidate["approval"] = receipt
        return receipt

    def shadow_receipt(self, candidate_id: str, runs: list[dict],
                       strategy: dict | None = None) -> dict:
        """Write a shadow-execution receipt (Phase 2 P2.5): candidate version,
        the exact strategy (prompt version + SHA256 + guards), the runtime
        config hash, and per-run model-output references. Shadow runs never
        touch the active config - this receipt only RECORDS what ran."""
        candidate = self.state.get(candidate_id)
        if candidate is None:
            raise EvolutionError(f"unknown candidate: {candidate_id}")
        proposal = candidate.get("proposal") or {}
        receipt = {
            "candidate_id": candidate_id,
            "candidate_version": len(candidate.get("history", [])),
            "kind": proposal.get("kind", "config_candidate"),
            "strategy": strategy or {
                "prompt_version": proposal.get("prompt_version"),
                "prompt_sha256": proposal.get("prompt_sha256"),
                "policy_guards": proposal.get("config_delta"),
            },
            "active_config": dict(self.active_config),
            "active_config_sha256": PromptRegistry.sha256(
                json.dumps(self.active_config, sort_keys=True)),
            "runs": runs,
            "simulation": self.simulation,
            "at": _now(),
        }
        target = self.receipts_dir / candidate_id
        target.mkdir(parents=True, exist_ok=True)
        (target / "shadow_tested.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        candidate["shadow"] = {"result": {"runs": len(runs)},
                               "receipt": str(target / "shadow_tested.json"),
                               "simulation": self.simulation}
        return receipt

    def _receipt(self, candidate_id: str, transition: str, payload: dict) -> None:
        target = self.receipts_dir / candidate_id
        target.mkdir(parents=True, exist_ok=True)
        receipt = {"candidate_id": candidate_id, "transition": transition,
                   "at": _now(), "simulation": self.simulation, **payload}
        (target / f"{transition}.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
