"""G5 integration: the two-phase evaluator against real V2 candidates.

Requires the V2 repository (read-only) and its venv python for the isolated
grader phase; skips cleanly when absent. Assertions cover mechanics and
denominators - never private values.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from genomeqc_agent.eval.adapter import map_report  # noqa: F401 - ensures eval pkg importable

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
V2_REPO = Path(os.environ.get("GENOMEQC_V2_REPO",
                              r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"))
CANDIDATES = V2_REPO / "data" / "organelle_v2" / "candidates"
V2_PYTHON = Path(os.environ.get(
    "GENOMEQC_V2_PYTHON",
    str(V2_REPO / "bench" / "inspect_adapter" / ".venv" / "Scripts" / "python.exe")))

pytestmark = pytest.mark.skipif(
    not (CANDIDATES.is_dir() and V2_PYTHON.is_file()),
    reason="V2 candidates or V2 venv python not available")


def _load_run_eval():
    """Load run_eval from scripts/ without making it a package."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "eval_offline", SCRIPTS / "eval_offline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_eval


def _run_eval(tmp_path: Path, limit: int, match: str) -> dict:
    # Phase A+B orchestration runs in-process; the grader itself is still a
    # subprocess under the V2 interpreter (that is the isolation boundary).
    return _load_run_eval()(CANDIDATES, tmp_path, limit, match, V2_PYTHON)


def test_evaluator_two_case_slice(tmp_path):
    """One normal + one coordinate fault case through both phases."""
    aggregate = _run_eval(tmp_path, limit=2, match="CHLORO-NORMAL-00")
    fault_aggregate = _run_eval(tmp_path / "b", limit=1, match="CHLORO-CDS-03")
    combined = {"denominators": {
        "cases_total": aggregate["denominators"]["cases_total"]
        + fault_aggregate["denominators"]["cases_total"],
        "graded": aggregate["denominators"]["graded"]
        + fault_aggregate["denominators"]["graded"],
        "skipped_not_mappable": aggregate["denominators"]["skipped_not_mappable"]
        + fault_aggregate["denominators"]["skipped_not_mappable"]}}
    # denominators stay honest
    assert combined["denominators"]["cases_total"] == 3
    assert combined["denominators"]["graded"] >= 1
    assert combined["denominators"]["cases_total"] == (
        combined["denominators"]["graded"]
        + combined["denominators"]["skipped_not_mappable"])
    # scorecards exist for graded cases and carry the frozen grader version
    for record in aggregate["records"] + fault_aggregate["records"]:
        if "scorecard" in record:
            scorecard = json.loads(Path(record["scorecard"]).read_text(encoding="utf-8"))
            assert scorecard["grader_version"].startswith(
                "organelle_v2.rubric.deterministic_grader/")
            assert scorecard["judgment"] in ("pass", "fail")
            assert scorecard["verdict"]["score"] in (0, 1)


def test_evaluator_aggregate_carries_no_private_values(tmp_path):
    aggregate = _run_eval(tmp_path, limit=1, match="CHLORO-CDS-03")
    text = json.dumps(aggregate)
    for token in ("defect_tokens", "acceptable_decisions", "mutation", "oracle_check"):
        assert token not in text, f"aggregate leaked {token}"
