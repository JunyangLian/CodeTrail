"""Tool-call contract.

Every tool result is machine-checkable: either ``result`` (deterministic
facts with evidence pointers) or a structured ``error``. Raw filesystem
paths, stack traces and provider secrets must never appear in results.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ToolErrorCode = Literal[
    "schema_invalid",
    "packet_not_found",
    "packet_unsupported",
    "path_denied",
    "budget_exceeded",
    "unsupported_structure",
    "transient_failure",
    "internal",
]


class ToolVersionInfo(BaseModel):
    tool_name: str
    tool_version: str = "1"
    tool_contract_version: str = "1"
    v2_adapter_version: str
    input_schema_hash: str = Field(min_length=64, max_length=64)


class ToolError(BaseModel):
    error_code: ToolErrorCode
    retryable: bool = False
    reason: str


class ToolRequest(BaseModel):
    run_id: str = Field(pattern=r"^[0-9a-fA-F-]{8,64}$")
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    tool_name: str
    ok: bool
    result: dict[str, Any] | None = None
    error: ToolError | None = None
    versions: ToolVersionInfo
