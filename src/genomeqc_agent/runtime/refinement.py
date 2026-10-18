"""Bounded evidence self-refinement (P1.1): mechanically diagnose and, when
provably safe, correct invalid evidence pointers - then re-verify.

Hard rules (user directive):
- at most ``max_rounds`` (=2) correction attempts;
- every accepted correction is RE-VERIFIED by the verify_evidence_pointer
  tool before it counts - a correction that does not resolve is discarded;
- locators are NEVER invented: only the FILE part may be corrected, and only
  via a unique filename-stem match against the freshly re-read tool manifest;
- unresolved pointers that cannot be mechanically corrected stay unresolved
  (the report stays partial / needs_human_review) - refinement can never
  delete necessary evidence nor force ``partial`` to ``valid``;
- the full before/after trace is recorded for audit.
"""
from __future__ import annotations

from pathlib import PurePosixPath

MAX_REFINEMENT_ROUNDS = 2


def _stem(path: str) -> str:
    """Filename with ALL extensions stripped (proteins.faa.txt ->
    proteins, artifacts/proteins.faa -> proteins): catches hallucinated
    suffixes and wrong-extension filenames alike. A unique stem match is
    only a CANDIDATE - acceptance always requires tool re-verification."""
    name = PurePosixPath(path).name
    return name.split(".")[0] or name


def classify_unresolved(pointer: str, manifest_paths: set[str]) -> dict:
    """Mechanically diagnose one unresolved pointer.

    Returns {reason, correction}: reason is file_not_in_manifest (a unique
    stem match proposes a candidate), locator_miss (file exists, locator
    does not - uncorrectable, we never invent locators), or
    malformed_pointer.
    """
    name, _, locator = pointer.partition(":")
    if not name or not locator:
        return {"reason": "malformed_pointer", "correction": None}
    if name in manifest_paths:
        return {"reason": "locator_miss", "correction": None}
    stem = _stem(name)
    matches = sorted(m for m in manifest_paths if _stem(m) == stem)
    if len(matches) == 1:
        return {"reason": "file_not_in_manifest",
                "correction": f"{matches[0]}:{locator}"}
    return {"reason": "file_not_in_manifest", "correction": None}


def plan_corrections(unresolved: list[str],
                     manifest_paths: set[str]) -> tuple[list[dict], list[dict]]:
    """Split unresolved pointers into mechanical correction candidates and
    uncorrectable ones. Deterministic; no model in the loop."""
    candidates: list[dict] = []
    uncorrectable: list[dict] = []
    for pointer in unresolved:
        diagnosis = classify_unresolved(pointer, manifest_paths)
        if diagnosis["correction"] is not None:
            candidates.append({"from": pointer,
                               "to": diagnosis["correction"],
                               "reason": diagnosis["reason"]})
        else:
            uncorrectable.append({"pointer": pointer,
                                  "reason": diagnosis["reason"]})
    return candidates, uncorrectable


def apply_verify_result(evidence: dict, accepted: list[dict],
                        rejected: list[dict]) -> dict:
    """Merge verification outcomes into the evidence report.

    Only pointers that the tool just re-verified as resolved leave the
    unresolved list; everything else stays exactly as it was (nothing is
    deleted, nothing is upgraded without mechanical proof).
    """
    accepted_map = {a["from"]: a["to"] for a in accepted}
    pointers = []
    for entry in evidence.get("pointers", []):
        pointer = entry["pointer"]
        if pointer in accepted_map:
            pointers.append({"pointer": accepted_map[pointer],
                             "resolved": True,
                             "locator_kind": entry.get("locator_kind"),
                             "corrected_from": pointer})
        else:
            pointers.append(dict(entry))
    unresolved = [p for p in evidence.get("unresolved", [])
                  if p not in accepted_map]
    return {"pointers": pointers, "unresolved": unresolved}
