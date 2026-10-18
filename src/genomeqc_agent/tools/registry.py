"""Tool registry: strict schema validation, budget accounting, error contract.

Tools are pure read-only functions over a PacketFiles snapshot. The registry
is the single execution gate: unknown tools, invalid arguments, exceeded
budgets and non-whitelisted paths all become structured ToolResult errors -
never exceptions leaking to callers.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from genomeqc_agent.adapters.v2_adapter import PacketFiles, SnapshotV2Port
from genomeqc_agent.config import DEFAULT_MAX_TOOL_CALLS, TOOL_CONTRACT_VERSION
from genomeqc_agent.schemas.tool import (
    ToolError,
    ToolRequest,
    ToolResult,
    ToolVersionInfo,
)

ErrorHandler = Callable[[Exception], ToolError]


class TransientToolError(RuntimeError):
    """A retryable tool failure (transport hiccup, temporary lock).

    Read-only tools are idempotent, so retrying is always safe; whether it
    is *worth it* is decided by the bounded-recovery router, not here.
    """


class ToolSpec:
    def __init__(self, name: str, args_model: type[BaseModel], runner: Callable[..., dict],
                 description: str):
        self.name = name
        self.args_model = args_model
        self.runner = runner
        self.description = description
        self.input_schema_hash = hashlib.sha256(
            json.dumps(args_model.model_json_schema(), sort_keys=True).encode()
        ).hexdigest()


class ToolRegistry:
    def __init__(self, port: SnapshotV2Port | None = None,
                 max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS):
        self._port = port or SnapshotV2Port()
        self._specs: dict[str, ToolSpec] = {}
        self._packets: dict[str, tuple[object, PacketFiles]] = {}
        self.max_tool_calls = max_tool_calls
        self.calls: dict[str, int] = {}

    # -- packet store ----------------------------------------------------
    def register_packet(self, packet_id: str, packet_dir) -> PacketFiles:
        if packet_id in self._packets:
            raise ValueError(f"packet already registered: {packet_id}")
        files = self._port.open_packet(packet_dir)
        self._packets[packet_id] = (packet_dir, files)
        return files

    def files_for(self, packet_id: str) -> PacketFiles:
        entry = self._packets.get(packet_id)
        if entry is None:
            raise KeyError(packet_id)
        return entry[1]

    # -- registration ----------------------------------------------------
    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec

    def spec(self, name: str) -> ToolSpec:
        return self._specs[name]

    def tool_names(self) -> list[str]:
        return sorted(self._specs)

    # -- execution -------------------------------------------------------
    def _version(self, spec: ToolSpec) -> ToolVersionInfo:
        return ToolVersionInfo(
            tool_name=spec.name,
            tool_contract_version=TOOL_CONTRACT_VERSION,
            v2_adapter_version=self._port.adapter_version,
            input_schema_hash=spec.input_schema_hash,
        )

    def _error(self, spec: ToolSpec | None, request: ToolRequest,
               code: str, retryable: bool, reason: str) -> ToolResult:
        if spec is None:  # unknown tool: synthesize a placeholder version block
            spec = ToolSpec(request.tool_name, ToolRequest, dict, "unknown")
        return ToolResult(
            tool_name=request.tool_name, ok=False,
            error=ToolError(error_code=code, retryable=retryable, reason=reason),
            versions=self._version(spec),
        )

    def execute(self, request: ToolRequest, packet_id: str) -> ToolResult:
        spec = self._specs.get(request.tool_name)
        if spec is None:
            return self._error(None, request, "schema_invalid", False,
                               f"unknown tool: {request.tool_name}")
        total = sum(self.calls.values())
        if total >= self.max_tool_calls:
            return self._error(spec, request, "budget_exceeded", False,
                               f"tool-call budget reached ({self.max_tool_calls})")
        try:
            args = spec.args_model.model_validate(request.arguments)
        except ValidationError as error:
            return self._error(spec, request, "schema_invalid", False,
                               f"arguments failed schema: {error.errors()[:3]}")
        try:
            files = self.files_for(packet_id)
        except KeyError:
            return self._error(spec, request, "packet_not_found", False,
                               f"packet not registered: {packet_id}")
        try:
            result = spec.runner(self._port, files, args)
        except ValueError as error:
            code = "path_denied" if "whitelist" in str(error) else "unsupported_structure"
            return self._error(spec, request, code, False, str(error))
        except TransientToolError as error:
            return self._error(spec, request, "transient_failure", True, str(error))
        except Exception as error:  # noqa: BLE001 - defensive tool boundary
            return self._error(spec, request, "internal", False,
                               f"{type(error).__name__}: {error}")
        self.calls[request.tool_name] = self.calls.get(request.tool_name, 0) + 1
        return ToolResult(tool_name=request.tool_name, ok=True, result=result,
                          versions=self._version(spec))
