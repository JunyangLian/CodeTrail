"""Static configuration for the offline G1 loop.

Env loading stays minimal until G2/G3 (LangGraph runtime + FastAPI) need it.
"""
from __future__ import annotations

import os
from pathlib import Path

# V2 baseline the snapshot is pinned to (G6 upgrade: v2.0.0-era 00e3424 ->
# stable handoff v2.1.1 @ 7518adc; byte parity of all snapshot sources was
# proven across the range before the bump - see docs/V2_TO_V3_UPGRADE_REPORT.md).
V2_SNAPSHOT_COMMIT = "7518adc"  # full hash in SNAPSHOT_MANIFEST.json
V2_BASELINE_TAG = "v2.1.1"
V2_ADAPTER_VERSION = f"v2.1.1-snapshot:{V2_SNAPSHOT_COMMIT}"
TOOL_CONTRACT_VERSION = "1"

SUPPORTED_DOMAINS = {"vertebrate_mitochondrion", "chloroplast", "auto_detect_supported"}

# Budget caps (single-run); V3 owns its own ledger, never V2 SESSION_CAPS.
DEFAULT_MAX_TOOL_CALLS = 200
DEFAULT_MAX_MODEL_REQUESTS = 50

# Packet public-view size limits, matching the audited V2 PublicFilesV2
# extension (inspect_integration/readonly.py): chloroplast genome.fa reaches
# ~160 KB, so the V1 128 KB limit would reject valid packets.
PACKET_FILE_LIMIT = 512_000
PACKET_PACKET_LIMIT = 1_500_000

REQUIRED_ARTIFACTS = ("genome.fa", "features.tsv", "proteins.faa", "sequence_stats.json")


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (KEY=VALUE lines); never overrides existing vars."""
    env_file = path or Path(".env")
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def model_env() -> dict:
    """Model gateway settings (load_dotenv first if you want .env values)."""
    return {
        "provider": os.environ.get("GENOMEQC_MODEL_PROVIDER", "fake"),
        "model_id": os.environ.get("GENOMEQC_MODEL_ID", "fake-planner-v0"),
        "api_base": os.environ.get("GENOMEQC_API_BASE", ""),
        "api_key": os.environ.get("GENOMEQC_API_KEY", ""),
    }


def data_dir() -> Path:
    return Path(os.environ.get("GENOMEQC_DATA_DIR", "./data")).resolve()


def runs_dir() -> Path:
    return data_dir() / "runs"
