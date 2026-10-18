"""inspect_packet: list the packet's public files with sizes and SHA256."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from genomeqc_agent.adapters.v2_adapter import PacketFiles, SnapshotV2Port
from genomeqc_agent.config import REQUIRED_ARTIFACTS


class InspectPacketArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def run(port: SnapshotV2Port, files: PacketFiles, args: InspectPacketArgs) -> dict:
    manifest = files.manifest()
    present = {entry["path"] for entry in manifest}
    return {
        "manifest": manifest,
        "required_artifacts_present": sorted(
            f"artifacts/{name}" for name in REQUIRED_ARTIFACTS
            if f"artifacts/{name}" in present
        ),
        "task_md_present": "task.md" in present,
        "packet_bytes": sum(entry["bytes"] for entry in manifest),
    }
