"""lookup_feature: exact feature rows by feature_id from the public TSV."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from genomeqc_agent.adapters.v2_adapter import PacketFiles, SnapshotV2Port


class LookupFeatureArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_id: str = Field(min_length=1, max_length=128)


def run(port: SnapshotV2Port, files: PacketFiles, args: LookupFeatureArgs) -> dict:
    columns, rows = files.table("artifacts/features.tsv")
    if "feature_id" not in columns:
        raise ValueError("features table lacks a feature_id column")
    matches = [
        {"pointer": f"artifacts/features.tsv:{number}", **row}
        for number, row in enumerate(rows, start=2)
        if row["feature_id"] == args.feature_id
    ]
    return {
        "feature_id": args.feature_id,
        "row_count": len(matches),
        "rows": matches,
        "feature_tsv_header": "artifacts/features.tsv:1",
    }
