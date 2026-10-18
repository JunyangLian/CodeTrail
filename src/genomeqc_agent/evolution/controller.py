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


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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

    def _receipt(self, candidate_id: str, transition: str, payload: dict) -> None:
        target = self.receipts_dir / candidate_id
        target.mkdir(parents=True, exist_ok=True)
        receipt = {"candidate_id": candidate_id, "transition": transition,
                   "at": _now(), "simulation": self.simulation, **payload}
        (target / f"{transition}.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
