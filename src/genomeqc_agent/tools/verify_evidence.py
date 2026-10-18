"""verify_evidence_pointer: mechanical locator resolution on public bytes.

Pointer syntax (V3 tool contract, mirroring the audited V2 semantics):
``<public-relative-path>:<locator>`` where locator is an existing top-level
JSON key (for .json targets), an existing 1-based line number, or any
substring of the target file. This checks locatability ONLY - whether a
pointer supports its claim stays a human/semantic-layer question.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from genomeqc_agent.adapters.v2_adapter import PacketFiles, SnapshotV2Port


class VerifyEvidenceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pointers: list[str] = Field(min_length=1, max_length=50)

    # V2 pointer form is <path>:<locator>; keep it single-line and bounded.
    # pydantic validates items at runtime; format is enforced in run().


def _valid_form(name: str) -> bool:
    """Mirror the snapshot path-form rules before any content access."""
    from pathlib import PurePosixPath
    return (isinstance(name, str) and "\\" not in name and "\x00" not in name
            and not PurePosixPath(name).is_absolute()
            and ".." not in PurePosixPath(name).parts
            and PurePosixPath(name).as_posix() == name)


def run(port: SnapshotV2Port, files: PacketFiles, args: VerifyEvidenceArgs) -> dict:
    whitelist = {entry["path"] for entry in files.manifest()}
    names: dict[str, str] = {}
    for pointer in args.pointers:
        name = pointer.partition(":")[0]
        if not name or name in names:
            continue
        if not _valid_form(name):
            # malformed/traversal path forms are rejected at tool level
            raise ValueError("pointer path is outside this sample public whitelist")
        if name in whitelist:
            names[name] = files.text(name)  # present files only; absent -> missing_file
    report = {"pointers": [], "unresolved": []}
    for pointer in args.pointers:
        name, _, locator = pointer.partition(":")
        if not name or not locator:
            entry = {"pointer": pointer, "resolved": False, "locator_kind": "malformed_pointer"}
            report["pointers"].append(entry)
            report["unresolved"].append(pointer)
            continue
        resolved, kind = port.resolve_pointer(name, locator, names)
        entry = {"pointer": pointer, "resolved": resolved, "locator_kind": kind}
        report["pointers"].append(entry)
        if not resolved:
            report["unresolved"].append(pointer)
    return report
