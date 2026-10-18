"""Answer-leakage negative tests (V3 side; mirrors the V2 isolation matrix).

G5 board blocker: 'private values never appear in public bytes' and
'task.md consistency across task types' - both enforced read-only.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from genomeqc_agent.adapters.v2_adapter import SnapshotV2Port

V3_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = V3_ROOT / "examples" / "public_packets"
V2_REPO = Path(os.environ.get("GENOMEQC_V2_REPO",
                              r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"))
CANDIDATES = V2_REPO / "data" / "organelle_v2" / "candidates"

# V2 test_no_answer_leakage.py matrix, verbatim.
FORBIDDEN_PUBLIC_SUBSTRINGS = ("acceptable_decisions", "defect_tokens",
                               "oracle_check", '"task_type"',
                               "declared_root_cause", "false positive")
FORBIDDEN_NAMES = {"meta.json", "expected.json", "rubric.json",
                   "mutation.json", "oracle_report.json"}


def test_v3_examples_carry_no_private_files():
    for packet_dir in EXAMPLES.iterdir():
        if not packet_dir.is_dir():
            continue
        names = {p.name.lower() for p in packet_dir.rglob("*") if p.is_file()}
        assert not (names & FORBIDDEN_NAMES), packet_dir.name
        assert not (packet_dir / "private").exists(), packet_dir.name


def test_v3_example_public_bytes_contain_no_leakage_markers():
    for packet_dir in EXAMPLES.iterdir():
        if not packet_dir.is_dir():
            continue
        files = SnapshotV2Port().open_packet(packet_dir)
        text = "".join(files.text(entry["path"]) for entry in files.manifest())
        for marker in FORBIDDEN_PUBLIC_SUBSTRINGS:
            assert marker not in text, f"{marker} leaked in {packet_dir.name}"


def test_v3_runtime_never_imports_private_grading_modules():
    """Static guard: the agent package must not import or invoke the frozen
    grader, oracle or private gold. (Names may legitimately appear inside
    the FORBIDDEN_NAMES blacklist - that is the defense, not a leak.)
    Grading lives only in the isolated worker process."""
    import re
    banned_patterns = (
        re.compile(r"import\s+.*\boracle\b"),
        re.compile(r"from\s+\S*oracle"),
        re.compile(r"(import|from)\s+\S*deterministic_grader"),
        re.compile(r"=?\s*grade_output\("),
        re.compile(r"[\"']private[\"']"),
        re.compile(r"private/(expected|rubric|mutation|oracle)"),
    )
    for py_file in (V3_ROOT / "src" / "genomeqc_agent").rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for pattern in banned_patterns:
            assert not pattern.search(text), \
                f"{py_file.name} matches banned pattern {pattern.pattern}"


@pytest.mark.skipif(not CANDIDATES.is_dir(), reason="V2 repo not available")
def test_v2_candidate_public_bytes_stay_clean_when_read_via_v3_sandbox():
    """Spot-check real V2 candidates through the V3 sandbox: private/ is
    invisible and public bytes carry no leakage markers."""
    checked = 0
    for case_dir in sorted(CANDIDATES.iterdir()):
        if not case_dir.is_dir():
            continue
        files = SnapshotV2Port().open_packet(case_dir)
        paths = {entry["path"] for entry in files.manifest()}
        assert not any(name in paths for name in FORBIDDEN_NAMES), case_dir.name
        text = "".join(files.text(p) for p in paths)
        for marker in FORBIDDEN_PUBLIC_SUBSTRINGS:
            assert marker not in text, f"{marker} leaked in {case_dir.name}"
        checked += 1
        if checked >= 5:
            break
    assert checked > 0


@pytest.mark.skipif(not CANDIDATES.is_dir(), reason="V2 repo not available")
def test_v2_candidate_task_md_identical_across_task_types():
    """V2 discipline: the task prompt must not hint whether a defect exists."""
    texts = {}
    for case_dir in sorted(CANDIDATES.iterdir()):
        if case_dir.is_dir():
            texts[case_dir.name] = (case_dir / "task.md").read_bytes()
    assert texts, "no candidates found"
    # all task.md contents byte-identical; only the set of contents matters
    distinct = {content for content in texts.values()}
    assert len(distinct) == 1, \
        f"task.md varies across {len(texts)} candidate cases ({len(distinct)} variants)"
