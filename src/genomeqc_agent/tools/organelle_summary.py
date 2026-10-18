"""organelle_summary: the audited V2 public-facts tool (pure snapshot code).

Computes counts, multipart groups, CDS/protein ID set differences, coordinate
bounds and claimed-vs-recomputed statistics from the public artifacts.
Returns measurements with evidence pointers - never a QC decision.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from genomeqc_agent.adapters.v2_adapter import PacketFiles, SnapshotV2Port
from genomeqc_agent.adapters.v2_snapshot.facts import FACTS_VERSION


class OrganelleSummaryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def run(port: SnapshotV2Port, files: PacketFiles, args: OrganelleSummaryArgs) -> dict:
    facts = port.public_facts(files)
    if facts.get("facts_version") != FACTS_VERSION:
        raise ValueError("unexpected facts version from snapshot")
    return facts
