"""FastAPI request/response contracts (plan §7.1)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from genomeqc_agent.config import (
    DEFAULT_MAX_MODEL_REQUESTS,
    DEFAULT_MAX_TOOL_CALLS,
    SUPPORTED_DOMAINS,
)

Domain = Literal["vertebrate_mitochondrion", "chloroplast", "auto_detect_supported"]


class JobSubmitRequest(BaseModel):
    packet_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    domain: Domain = "auto_detect_supported"
    user_goal: str = Field(default="", max_length=4000)
    # Budgets may only tighten against service defaults (plan §7.2).
    max_tool_calls: int = Field(default=DEFAULT_MAX_TOOL_CALLS, ge=1, le=DEFAULT_MAX_TOOL_CALLS)
    max_model_requests: int = Field(default=DEFAULT_MAX_MODEL_REQUESTS, ge=1,
                                   le=DEFAULT_MAX_MODEL_REQUESTS)
    max_retries_per_tool: int = Field(default=1, ge=0, le=3)
    # P4 model/strategy selection: "llm" requires explicit paid-run
    # authorisation (GENOMEQC_PAID_AUTHORIZED=true) and is rejected otherwise.
    planner_kind: Literal["scripted", "llm"] = "scripted"
    model_id: str | None = None
    prompt_version: str = "promptv1"
    policy_guards: dict[str, bool] = Field(default_factory=dict)
    features: dict[str, bool] = Field(default_factory=dict)


class JobSubmitted(BaseModel):
    run_id: str
    status: str = "queued"
    location: str


class JobStatus(BaseModel):
    run_id: str
    packet_id: str
    domain: str
    status: str
    qc_decision: str | None = None
    evidence_validity: str | None = None
    report_ref: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str


class EventLine(BaseModel):
    step: int
    node: str
    summary: str


class EventsResponse(BaseModel):
    run_id: str
    events: list[EventLine]


class ReportResponse(BaseModel):
    run_id: str
    format: str
    report: Any


class PacketInfo(BaseModel):
    packet_id: str
    packet_dir: str
    input_sha256: str
    source: str


class PacketRegistered(BaseModel):
    packet_id: str
    input_sha256: str
    artifact_files: list[str]
    reused: bool


class SimpleOk(BaseModel):
    run_id: str
    outcome: str


class Healthz(BaseModel):
    status: str = "ok"
    version: str
    supported_domains: list[str] = sorted(SUPPORTED_DOMAINS)


# -- P4: evidence / refinement / strategy endpoints ---------------------------

class EvidenceResponse(BaseModel):
    run_id: str
    evidence: dict
    tool_trace: dict


class RefinementResponse(BaseModel):
    run_id: str
    refinement: dict | None


class StrategyVersionInfo(BaseModel):
    version: str
    sha256: str
    registered_at: str = ""
    author: str = ""
    notes: str = ""


class StrategyVersionsResponse(BaseModel):
    active_prompt_version: str
    active_config: dict
    paid_runs_authorized: bool
    versions: list[StrategyVersionInfo]
    prompt_diff_v1_v2: str = ""
    candidates: list[dict] = Field(default_factory=list)


class ApprovalRecord(BaseModel):
    candidate_id: str
    approver: str
    decision: str
    real: bool
    at: str
    simulation: str = ""
    candidate_version: int = 1


class StrategyApprovalsResponse(BaseModel):
    approvals: list[ApprovalRecord]
