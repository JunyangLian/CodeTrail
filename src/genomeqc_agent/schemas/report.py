"""QC report contract (V3).

Decision enum adapts the V2 public verdict contract
(pass/warn/block/rollback) to the V3 report contract required by the master
plan (pass/warn/block/insufficient_evidence): V2 ``rollback`` maps onto
``block`` at composition time and every finding stays traceable to public
tool evidence.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

QCDecision = Literal["pass", "warn", "block", "insufficient_evidence"]
EvidenceValidity = Literal["valid", "partial", "invalid", "not_applicable"]
FindingStatus = Literal["pass", "warn", "block", "insufficient_evidence"]
Verification = Literal["verified_by_tool", "tool_unavailable", "not_verified"]


class Finding(BaseModel):
    check_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    status: FindingStatus
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    verification: Verification = "verified_by_tool"


class QCReport(BaseModel):
    run_id: str
    packet: dict  # PacketMeta-compatible dict
    domain: str
    model_id: str
    qc_decision: QCDecision
    evidence_validity: EvidenceValidity
    findings: list[Finding] = Field(min_length=1)
    not_checked: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
    tool_calls: dict[str, int] = Field(default_factory=dict)
    trace_ref: str | None = None
    versions: dict[str, str] = Field(default_factory=dict)


class RunReceipt(BaseModel):
    """One append-only audit line per run (JSONL)."""

    run_id: str
    started_at: str
    finished_at: str | None = None
    outcome: str = "running"
    packet_id: str
    input_sha256: str
    tool_calls: dict[str, int] = Field(default_factory=dict)
    report_sha256: str | None = None
    versions: dict[str, str] = Field(default_factory=dict)
