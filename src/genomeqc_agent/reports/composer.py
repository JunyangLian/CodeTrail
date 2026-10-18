"""Deterministic QC report composer: findings only from verified tool facts.

G1 rule set maps the organelle_summary fact groups onto check findings. A
missing or unusable facts structure becomes ``insufficient_evidence`` - the
composer never reports "all normal" without verified facts.
"""
from __future__ import annotations

from genomeqc_agent import __version__
from genomeqc_agent.schemas.report import Finding, QCReport
from genomeqc_agent.schemas.tool import ToolResult

NOT_CHECKED = [
    "translation correctness against the genetic code (tool computes presence, not semantics)",
    "annotation semantics versus external references (no external database access)",
    "cross-packet provenance (single packet is self-checked only)",
    "biological plausibility of feature content",
]


def compose_findings(facts: dict | None) -> list[Finding]:
    if facts is None:
        return [Finding(
            check_id="organelle_facts_unavailable",
            status="insufficient_evidence",
            summary="organelle_summary could not produce facts from this packet; "
                    "no QC claim is made",
        )]
    findings: list[Finding] = []

    violations = facts.get("coordinate_violations") or []
    if violations:
        first = violations[0]
        detail = (f"(e.g. {first.get('feature_id', '?')} spans "
                  f"{first.get('start', '?')}..{first.get('end', '?')} while the "
                  f"sequence length is {first.get('actual_sequence_length', '?')})")
    else:
        detail = ""
    findings.append(Finding(
        check_id="coordinate_bounds",
        status="block" if violations else "pass",
        summary=(f"{len(violations)} feature row(s) fall outside the genome "
                 f"sequence bounds {detail}"
                 if violations else "all feature coordinates lie within the sequence"),
        evidence_refs=[v["pointer"] for v in violations],
    ))

    issues = facts.get("multipart_structure_issues") or []
    if issues:
        named = ", ".join(i.get("feature_id", "?") for i in issues[:3])
        detail = f"(flagged features: {named})"
    else:
        detail = ""
    findings.append(Finding(
        check_id="multipart_structure",
        status="block" if issues else "pass",
        summary=(f"{len(issues)} multi-part feature(s) with broken part_index/"
                 f"part_count layout {detail}"
                 if issues else "multi-part feature layout is consistent"),
        evidence_refs=[i["pointer"] for i in issues],
    ))

    missing_faa = facts.get("cds_protein_ids_without_fasta_record") or []
    orphan_faa = facts.get("fasta_protein_ids_without_cds") or []
    refs = [p for item in missing_faa for p in item.get("pointers", [])]
    refs += [item.get("pointer") for item in orphan_faa if item.get("pointer")]
    if missing_faa or orphan_faa:
        named = ", ".join([item.get("protein_id", "?") for item in missing_faa[:3]]
                          + [item.get("protein_id", "?") for item in orphan_faa[:3]])
        summary = (f"CDS/protein ID set mismatch: {len(missing_faa)} CDS protein_id(s) "
                   f"without a proteins.faa record and {len(orphan_faa)} FASTA record(s) "
                   f"without a CDS (e.g. {named})")
    else:
        summary = "CDS protein_id set equals proteins.faa record set"
    findings.append(Finding(
        check_id="cross_file_protein_id",
        status="block" if (missing_faa or orphan_faa) else "pass",
        summary=summary,
        evidence_refs=refs,
    ))

    all_stats = sorted((facts.get("statistics") or {}).items())
    mismatched = [pair for _, pair in all_stats if pair.get("equal") is False]
    # A pass claim cites the FULL fact set it rests on (master plan §3: the
    # evidence must support the conclusion, not merely locate it); a block
    # cites the same set and names the contradicting keys in the summary.
    findings.append(Finding(
        check_id="stats_consistency",
        status="block" if mismatched else "pass",
        summary=("declared statistics contradict recomputation for: "
                 + ", ".join(pair["pointer"].split(":")[-1] for pair in mismatched)
                 if mismatched else "every declared statistic matches recomputation"),
        evidence_refs=[pair["pointer"] for _, pair in all_stats],
    ))
    return findings


def qc_decision(findings: list[Finding]) -> str:
    statuses = {finding.status for finding in findings}
    if "block" in statuses:
        return "block"
    if "insufficient_evidence" in statuses:
        return "insufficient_evidence"
    if "warn" in statuses:
        return "warn"
    return "pass"


def compose_report(run_id: str, packet_meta: dict, domain: str, model_id: str,
                   tool_results: list[ToolResult], facts: dict | None,
                   versions: dict[str, str], unresolved_count: int = 0,
                   tool_calls: dict[str, int] | None = None,
                   unresolved_requires_review: bool = False) -> QCReport:
    findings = compose_findings(facts)
    if facts is None:
        evidence_validity = "invalid"
    elif unresolved_count:
        evidence_validity = "partial"
    else:
        evidence_validity = "valid"
    merged_versions = {"agent": f"v3-g1 ({__version__})", "model": model_id, **versions}
    return QCReport(
        run_id=run_id,
        packet=packet_meta,
        domain=domain,
        model_id=model_id,
        qc_decision=qc_decision(findings),
        evidence_validity=evidence_validity,
        findings=findings,
        not_checked=list(NOT_CHECKED),
        # Phase 2 guard (unresolved_requires_review): unresolved pointers
        # escalate to human review. Baseline (guard off) keeps the historical
        # False - the guard can only escalate, never silence.
        needs_human_review=bool(unresolved_requires_review and unresolved_count),
        tool_calls=(tool_calls if tool_calls is not None else
                    {name: sum(1 for r in tool_results if r.tool_name == name and r.ok)
                     for name in {r.tool_name for r in tool_results}}),
        versions=merged_versions,
    )


def render_markdown(report: QCReport) -> str:
    lines = [
        f"# QC Report {report.run_id}",
        "",
        f"- decision: **{report.qc_decision}**",
        (f"- packet: `{report.packet.get('packet_id', '?')}` "
         f"(input sha256 `{report.packet.get('input_sha256', '?')[:12]}…`)"),
        f"- domain: {report.domain} ｜ model: `{report.model_id}`",
        "",
        "## Findings",
        "",
    ]
    for finding in report.findings:
        lines.append(f"### {finding.check_id} — {finding.status}")
        lines.append(finding.summary)
        if finding.evidence_refs:
            lines.append("")
            lines.append("Evidence:")
            for ref in finding.evidence_refs:
                lines.append(f"- `{ref}`")
        lines.append("")
    lines.append("## Not checked")
    lines.extend(f"- {item}" for item in report.not_checked)
    lines += ["", "## Versions"]
    lines += [f"- {key}: {value}" for key, value in sorted(report.versions.items())]
    return "\n".join(lines) + "\n"
