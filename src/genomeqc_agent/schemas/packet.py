"""Packet intake contract.

A V3 packet mirrors the audited V2 case layout: ``task.md`` plus an
``artifacts/`` directory (public view only). Single GenBank uploads are
converted into this layout by adapters/packet_adapter before intake.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from genomeqc_agent.config import SUPPORTED_DOMAINS

Domain = Literal["vertebrate_mitochondrion", "chloroplast", "auto_detect_supported"]


class PacketIntake(BaseModel):
    """User-facing submission description (validated at intake)."""

    packet_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    packet_dir: str
    domain: Domain = "auto_detect_supported"
    user_goal: str = Field(default="", max_length=4000)

    @field_validator("domain")
    @classmethod
    def domain_supported(cls, value: str) -> str:
        if value not in SUPPORTED_DOMAINS:
            raise ValueError(f"unsupported domain: {value}")
        return value


class PacketMeta(BaseModel):
    """Derived packet identity recorded on every run."""

    packet_id: str
    packet_dir: str
    input_sha256: str = Field(min_length=64, max_length=64)
    domain: Domain
    task_md_sha256: str | None = None
    artifact_files: list[str] = Field(default_factory=list)
