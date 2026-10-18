"""Offline smoke pipeline: public input -> tool facts -> structured QC finding.

Scripted FakeModel plan -> ToolRegistry (strict validation, budgets) ->
deterministic report composer. No network, no real model, no private gold.
The plan branches on tool output (multipart issues trigger a lookup_feature
call), demonstrating evidence-driven tool selection with zero LLM calls.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from genomeqc_agent.adapters.packet_adapter import packet_identity
from genomeqc_agent.adapters.v2_adapter import V2_ADAPTER_VERSION
from genomeqc_agent.config import TOOL_CONTRACT_VERSION
from genomeqc_agent.model.fake_model import FakeModel
from genomeqc_agent.reports.composer import compose_report, render_markdown
from genomeqc_agent.schemas.packet import PacketMeta
from genomeqc_agent.schemas.tool import ToolRequest
from genomeqc_agent.tools.inspect_packet import InspectPacketArgs
from genomeqc_agent.tools.inspect_packet import run as inspect_packet_run
from genomeqc_agent.tools.lookup_feature import LookupFeatureArgs
from genomeqc_agent.tools.lookup_feature import run as lookup_feature_run
from genomeqc_agent.tools.organelle_summary import OrganelleSummaryArgs
from genomeqc_agent.tools.organelle_summary import run as organelle_summary_run
from genomeqc_agent.tools.registry import ToolRegistry, ToolSpec
from genomeqc_agent.tools.verify_evidence import VerifyEvidenceArgs
from genomeqc_agent.tools.verify_evidence import run as verify_evidence_run

FACT_KEYS = ("coordinate_violations", "multipart_structure_issues",
             "cds_protein_ids_without_fasta_record", "fasta_protein_ids_without_cds",
             "statistics")


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec("inspect_packet", InspectPacketArgs, inspect_packet_run,
                               "List public files with sizes and SHA256."))
    registry.register(ToolSpec("organelle_summary", OrganelleSummaryArgs, organelle_summary_run,
                               "Compute organelle public facts with evidence pointers."))
    registry.register(ToolSpec("lookup_feature", LookupFeatureArgs, lookup_feature_run,
                               "Fetch exact feature rows by feature_id from features.tsv."))
    registry.register(ToolSpec("verify_evidence_pointer", VerifyEvidenceArgs, verify_evidence_run,
                               "Resolve evidence pointers against public packet bytes."))
    return registry


def _evidence_pointers(facts: dict) -> list[str]:
    """Collect artifact-file pointers from facts (tool fields are NOT artifacts)."""
    pointers: list[str] = []
    pointers += [v["pointer"] for v in facts.get("coordinate_violations") or []]
    pointers += [v["pointer"] for v in facts.get("multipart_structure_issues") or []]
    pointers += [p for item in facts.get("cds_protein_ids_without_fasta_record") or []
                 for p in item.get("pointers", [])]
    pointers += [item["pointer"] for item in facts.get("fasta_protein_ids_without_cds") or []]
    pointers += [pair["pointer"] for pair in (facts.get("statistics") or {}).values()]
    return pointers


def run_smoke(packet_dir: str | Path, domain: str = "auto_detect_supported",
              out_dir: str | Path | None = None, run_id: str | None = None,
              registry: ToolRegistry | None = None) -> dict:
    packet_dir = Path(packet_dir)
    run_id = run_id or str(uuid.uuid4())
    started_at = datetime.now(UTC).isoformat()

    meta: PacketMeta = packet_identity(packet_dir.name, packet_dir, domain)
    registry = registry or default_registry()
    try:
        registry.register_packet(meta.packet_id, packet_dir)
    except ValueError:
        pass  # already registered on this registry (repeat submissions are idempotent)
    files = registry.files_for(meta.packet_id)
    del files  # tools receive the snapshot via the registry, not directly

    model = FakeModel()
    plan = model.plan("initial_plan", {"packet_id": meta.packet_id, "domain": domain})

    tool_results = []
    facts: dict | None = None
    for call in plan:
        result = registry.execute(
            ToolRequest(run_id=run_id, tool_name=call.tool_name, arguments=call.arguments),
            meta.packet_id)
        tool_results.append(result)
        if not result.ok:
            break
        if call.tool_name == "organelle_summary":
            facts = result.result
            issues = (facts or {}).get("multipart_structure_issues") or []
            if issues:
                # Evidence-driven branch: inspect the first flagged feature_id.
                followup = registry.execute(
                    ToolRequest(run_id=run_id, tool_name="lookup_feature",
                                arguments={"feature_id": issues[0]["feature_id"]}),
                    meta.packet_id)
                tool_results.append(followup)

    # Evidence verification: every pointer the report cites must resolve.
    pointer_report = None
    if facts is not None and all(key in facts for key in FACT_KEYS):
        pointers = _evidence_pointers(facts)[:50]
        if pointers:
            result = registry.execute(
                ToolRequest(run_id=run_id, tool_name="verify_evidence_pointer",
                            arguments={"pointers": pointers}),
                meta.packet_id)
            tool_results.append(result)
            pointer_report = result.result if result.ok else None

    unresolved = (pointer_report or {}).get("unresolved", [])
    report = compose_report(
        run_id=run_id,
        packet_meta=meta.model_dump(),
        domain=domain,
        model_id=model.model_id,
        tool_results=tool_results,
        facts=facts,
        unresolved_count=len(unresolved),
        versions={"tool_contract": TOOL_CONTRACT_VERSION, "v2_adapter": V2_ADAPTER_VERSION},
    )

    report_json = (report.model_dump_json(indent=2) + "\n").encode("utf-8")
    report_sha256 = hashlib.sha256(report_json).hexdigest()
    receipt = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "outcome": report.qc_decision,
        "packet_id": meta.packet_id,
        "input_sha256": meta.input_sha256,
        "tool_calls": dict(registry.calls),
        "report_sha256": report_sha256,
        "versions": report.versions,
        "unresolved_pointers": unresolved,
    }

    written: dict[str, str] | None = None
    if out_dir is not None:
        run_dir = Path(out_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "QC_REPORT.json").write_bytes(report_json)
        (run_dir / "QC_REPORT.md").write_text(render_markdown(report), encoding="utf-8")
        (run_dir / "RUN_RECEIPT.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        written = {
            "qc_report_json": str(run_dir / "QC_REPORT.json"),
            "qc_report_md": str(run_dir / "QC_REPORT.md"),
            "run_receipt": str(run_dir / "RUN_RECEIPT.json"),
        }
    return {"report": report.model_dump(), "receipt": receipt, "written": written,
            "model_requests": model.request_count}
