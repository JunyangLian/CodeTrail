"""Deterministic QCReport -> V2 model-output-contract adapter.

The V2 frozen grader (organelle_v2.rubric.deterministic_grader/v1) scores the
contract: verdict/observed_defect/root_cause/evidence/action. The mapping is
mechanical and loss-aware: V3 verdicts or defect classes that have NO honest
counterpart in the V2 contract mark the case not mappable instead of
guessing, and the evaluator keeps them in the denominator as skipped.
"""
from __future__ import annotations

from typing import Any

# V3 check_id -> V2 root_cause token. multipart_structure has NO honest V2
# token (V2 enum: none/cross_file_id_mismatch/coordinate_out_of_bounds/
# stats_conflict) and never maps.
ROOT_CAUSE_MAP = {
    "coordinate_bounds": "coordinate_out_of_bounds",
    "cross_file_protein_id": "cross_file_id_mismatch",
    "stats_consistency": "stats_conflict",
}
VERDICT_MAP = {"pass": "pass", "warn": "warn", "block": "block"}


def map_report(report: dict) -> dict:
    """Map one QCReport dict into the V2 contract.

    Returns {mappable, reason, output} where output is the V2-contract object
    (only when mappable) ready to be serialized as the grader input text.
    """
    verdict = report.get("qc_decision")
    if verdict == "insufficient_evidence":
        return {"mappable": False,
                "reason": "insufficient_evidence has no honest V2 verdict counterpart",
                "output": None}
    if verdict not in VERDICT_MAP:
        return {"mappable": False, "reason": f"unknown verdict: {verdict}",
                "output": None}
    blocked = [f for f in report.get("findings", []) if f.get("status") == "block"]
    causes = {ROOT_CAUSE_MAP[f["check_id"]] for f in blocked
              if f["check_id"] in ROOT_CAUSE_MAP}
    unmapped = [f["check_id"] for f in blocked
                if f["check_id"] not in ROOT_CAUSE_MAP]
    if unmapped:
        return {"mappable": False,
                "reason": f"blocked checks without V2 root_cause token: {unmapped}",
                "output": None}
    if len(causes) > 1:
        return {"mappable": False,
                "reason": f"multi-block report spans several root causes: {sorted(causes)}",
                "output": None}
    root_cause = causes.pop() if causes else "none"
    evidence: list[dict[str, Any]] = []
    if blocked:
        for finding in blocked:
            for ref in finding.get("evidence_refs", []):
                evidence.append({"pointer": ref, "note": finding["check_id"]})
    else:
        for finding in report.get("findings", []):
            for ref in finding.get("evidence_refs", []):
                evidence.append({"pointer": ref, "note": finding["check_id"]})
    if not evidence:  # contract requires minItems 1
        return {"mappable": False,
                "reason": "report carries no resolvable evidence pointers",
                "output": None}
    observed = "; ".join(f"{f['check_id']}: {f['summary']}" for f in blocked)
    action = ("withhold delivery until every blocked finding is resolved"
              if verdict == "block" else
              "deliverable; review warnings before release"
              if verdict == "warn" else "deliverable as-is")
    return {"mappable": True, "reason": "",
            "output": {"verdict": VERDICT_MAP[verdict],
                       "observed_defect": observed,
                       "root_cause": root_cause,
                       "evidence": evidence,
                       "action": action}}
