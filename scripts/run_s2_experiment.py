"""S2 formal experiment (方案甲, user-authorized 2026-10-18): 120
observations = 40 frozen DEV cases x {GLM A, GLM B, DeepSeek A}.

Budget protection (P2):
- global token cap for S2 = 901,149 (1,000,000 project cap - 98,851 S1);
- global HTTP cap = 390 (frozen protocol per-arm 130 x 3 arms);
- ONE ledger object for the whole process - no subprocess resets
  (grader worker subprocesses consume zero model tokens);
- worst-case reservation before EVERY observation (5 req x 8192 max_tokens
  + prompt allowance), released down to actual usage afterwards;
- every one of the 120 positions appears in the final ledger, including
  failed or budget-skipped positions;
- resume: completed cells are detected from the ledger and never re-run.

Metering: usage.total_tokens per successful chat call, exactly as returned
by the provider; reasoning tokens are inside completion_tokens and are
counted ONCE; failed HTTP attempts count as requests with 0 tokens.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent import config
from genomeqc_agent.eval.adapter import map_report
from genomeqc_agent.evolution.budget import BudgetGuard
from genomeqc_agent.evolution.prompts import PromptRegistry
from genomeqc_agent.model.gateway import ChatGateway, GatewayError
from genomeqc_agent.model.llm_planner import LLMPlanner, PlannerError
from genomeqc_agent.runtime.graph import QCGraph

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "s2_experiment"
STATE = ROOT / "evolution_state"
V2_CANDIDATES = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"
                     ) / "data" / "organelle_v2" / "candidates"
V2_PYTHON = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"
                 r"\bench\inspect_adapter\.venv\Scripts\python.exe")
GRADER_WORKER = ROOT / "scripts" / "eval_grader_worker.py"
LEDGER_PATH = ROOT / "V3_S2_RUN_LEDGER.jsonl"
PLAN_PATH = ROOT / "V3_S2_FROZEN_PLAN.json"

S1_TOKENS = 98_851
PROJECT_CAP = 1_000_000
S2_TOKEN_CAP = PROJECT_CAP - S1_TOKENS          # 901,149
PER_ARM_REQUEST_CAP = 130
GLOBAL_REQUEST_CAP = PER_ARM_REQUEST_CAP * 3    # 390
MAX_REQUESTS_PER_RUN = 5
WORST_CASE_TOKENS_PER_RUN = MAX_REQUESTS_PER_RUN * 8192 + 5 * 2_000  # 50,960

MODELS = {"glm": "zai-org/GLM-5.3-Flash", "ds": "deepseek-ai/DeepSeek-V4.1-Flash"}
GUARDS_B = {"evidence_enforce_manifest": True, "pre_verify_pointers": True,
            "unresolved_requires_review": True}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _authorize() -> None:
    import os
    if os.environ.get("GENOMEQC_PAID_AUTHORIZED") != "true":
        raise SystemExit("BLOCKED: GENOMEQC_PAID_AUTHORIZED=true must be set by "
                         "the caller; the script never self-authorizes")


def _frozen_cases() -> list[str]:
    """The G4-frozen 40-case DEV list (identical slice, same order source)."""
    agg = json.loads((ROOT / "data" / "eval" / "g4_ds_chloro40" / "aggregate.json")
                     .read_text(encoding="utf-8"))
    cases = [rec["case"] for rec in agg["records"]]
    assert len(cases) == 40, f"expected 40 frozen cases, got {len(cases)}"
    return cases


def _source_group(case_id: str) -> str:
    m = re.search(r"(NC_\d+_\d+)", case_id)
    return m.group(1) if m else "unknown"


def _run_observation(planner: LLMPlanner, case_id: str, packet_dir: str,
                     strategy: dict, db_path: Path) -> tuple[dict, int, int]:
    run_id = str(uuid.uuid4())
    graph = QCGraph(db_path=db_path, out_dir=OUT / "runs", planner=planner)
    try:
        started = time.monotonic()
        state = graph.run(run_id, case_id, packet_dir,
                          max_model_requests=MAX_REQUESTS_PER_RUN,
                          strategy=strategy)
        elapsed = round(time.monotonic() - started, 1)
    finally:
        graph.close()
    usage = (state.get("runtime") or {}).get("model_usage") or []
    tokens = sum(int(u.get("total_tokens") or 0) for u in usage)
    runtime = state.get("runtime") or {}
    result = state.get("result") or {}
    report = state.get("report") or {}
    obs = {
        "run_id": run_id, "status": runtime.get("status"),
        "qc_decision": result.get("qc_decision"),
        "evidence_validity": result.get("evidence_validity"),
        "needs_human_review": report.get("needs_human_review"),
        "unresolved_pointers": len((state.get("evidence") or {})
                                   .get("unresolved", [])),
        "guard_denials": [e["summary"] for e in state.get("events", [])
                          if e["node"] == "policy_gate"
                          and e["summary"].startswith("denied")],
        "prompt_version": strategy["prompt_version"],
        "prompt_sha256": strategy["prompt_sha256"],
        "tokens": tokens, "requests": len(usage), "elapsed_s": elapsed,
        "raw_error": (runtime.get("last_error") or "")[:500],
        "tool_calls": {k: len(v) for k, v in
                       (state.get("tool_trace") or {}).items()},
    }
    return obs, tokens, len(usage)


def _grade(case_id: str, report: dict, cell: str) -> dict:
    mapped = map_report(report)
    if not mapped["mappable"]:
        return {"mappable": False, "skip_reason": mapped["reason"]}
    cell_dir = OUT / "grading" / cell.replace(":", "__")  # ':' illegal on Windows
    cell_dir.mkdir(parents=True, exist_ok=True)
    v2_output = cell_dir / "v2_output.json"
    v2_output.write_text(json.dumps(mapped["output"], ensure_ascii=False, indent=2),
                         encoding="utf-8")
    scorecard_path = cell_dir / "scorecard.json"
    proc = subprocess.run(
        [str(V2_PYTHON), str(GRADER_WORKER), "--case", str(V2_CANDIDATES / case_id),
         "--input", str(v2_output), "--out", str(scorecard_path)],
        capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        return {"mappable": True, "grader_error": proc.stderr[-300:]}
    sc = json.loads(scorecard_path.read_text(encoding="utf-8"))
    return {"mappable": True,
            "judgment": sc.get("judgment"),
            "verdict_score": sc.get("verdict", {}).get("score"),
            "output_contract_ok": sc.get("output_contract", {}).get("score") == 1,
            "defect_localization": (sc.get("defect_localization")
                                    or sc.get("defect_localisation"))}


def main() -> int:
    config.load_dotenv()
    _authorize()
    OUT.mkdir(parents=True, exist_ok=True)
    registry = PromptRegistry(STATE / "prompt_registry.v1.json")
    strategies = {
        "glm_A": {"prompt_version": "promptv1",
                  "prompt_sha256": registry.get("promptv1")["sha256"],
                  "policy_guards": {}},
        "glm_B": {"prompt_version": "promptv2",
                  "prompt_sha256": registry.get("promptv2")["sha256"],
                  "policy_guards": dict(GUARDS_B)},
        "ds_A": {"prompt_version": "promptv1",
                 "prompt_sha256": registry.get("promptv1")["sha256"],
                 "policy_guards": {}},
    }
    cases = _frozen_cases()
    # frozen execution order: per case glm/A -> glm/B -> ds/A (A/B alternation
    # balanced within each case; case order = frozen G4 slice order)
    cells = [(c, arm) for c in cases
             for arm in ("glm_A", "glm_B", "ds_A")]

    if not PLAN_PATH.exists():
        plan = {
            "plan": "V3_S2_FROZEN_PLAN/v1", "frozen_at": _now(),
            "authorization": "user directive 2026-10-18: S2 方案甲 (frozen protocol)",
            "matrix": {"arms": {"glm_A": "GLM-5.3-Flash promptv1",
                                "glm_B": "GLM-5.3-Flash promptv2+guards",
                                "ds_A": "DeepSeek-V4.1-Flash promptv1 (reference)"},
                       "observations": 120,
                       "cases": cases,
                       "execution_order": [f"{c}:{a}" for c, a in cells]},
            "budget": {"project_cap": PROJECT_CAP, "s1_consumed": S1_TOKENS,
                       "s2_token_cap": S2_TOKEN_CAP,
                       "per_arm_request_cap": PER_ARM_REQUEST_CAP,
                       "global_request_cap": GLOBAL_REQUEST_CAP,
                       "metering": ("usage.total_tokens per successful chat call; "
                                    "reasoning counted once inside completion; "
                                    "failed attempts = requests, 0 tokens"),
                       "reservation": "worst-case per observation before start"},
            "discipline": {"no_best_of": True, "no_auto_retry": True,
                           "repair_separately_listed": True,
                           "no_flag_enabling": ["self_refinement", "data_flywheel"],
                           "no_prompt_or_scorer_change": True,
                           "dev_evaluation_only": True},
        }
        PLAN_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        print(f"frozen plan: {PLAN_PATH}", flush=True)

    # resume: completed cells from the ledger; the budget guard is seeded
    # from the SAME ledger so a restarted process never resets the global
    # budget accounting (P2.4)
    done: set[tuple[str, str]] = set()
    prior_tokens = prior_requests = 0
    if LEDGER_PATH.exists():
        for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("kind") == "observation" and rec.get("status") not in (
                        None, "interrupted"):
                    done.add((rec["case_id"], rec["arm"]))
                    prior_tokens += int(rec.get("tokens") or 0)
                    prior_requests += int(rec.get("requests") or 0)
    print(f"resume: {len(done)}/120 cells already complete "
          f"({prior_tokens} tok, {prior_requests} req already in ledger)",
          flush=True)

    gateways = {m: ChatGateway(api_key=config.model_env()["api_key"],
                               api_base=config.model_env()["api_base"],
                               model_id=mid, timeout=120.0)
                for m, mid in MODELS.items()}
    planners = {}
    for arm, strat in strategies.items():
        model_key = arm.split("_")[0]
        planners[arm] = LLMPlanner(gateways[model_key], max_tokens=8192,
                                   prompt_registry=registry,
                                   prompt_version=strat["prompt_version"])

    guard = BudgetGuard(S2_TOKEN_CAP, GLOBAL_REQUEST_CAP)
    guard.tokens = prior_tokens      # seeded from the ledger (no reset)
    guard.requests = prior_requests
    completed = len(done)
    aborted_budget = False
    with LEDGER_PATH.open("a", encoding="utf-8") as ledger_fh:
        for case_id, arm in cells:
            if (case_id, arm) in done:
                continue
            if not guard.reserve(WORST_CASE_TOKENS_PER_RUN, MAX_REQUESTS_PER_RUN):
                # every remaining position still gets a ledger line
                rec = {"kind": "observation", "case_id": case_id, "arm": arm,
                       "status": "skipped_due_to_budget", "tokens": 0,
                       "requests": 0, "at": _now(),
                       "budget": guard.snapshot()}
                ledger_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                ledger_fh.flush()
                print(f"[BUDGET STOP] {case_id}:{arm} "
                      f"({guard.snapshot()['tokens_remaining_effective']} tok "
                      f"effective remaining < worst case)", flush=True)
                aborted_budget = True
                continue
            model_key = arm.split("_")[0]
            packet_dir = str(V2_CANDIDATES / case_id)
            label = f"{case_id}:{arm}"
            try:
                obs, tokens, requests = _run_observation(
                    planners[arm], case_id, packet_dir, strategies[arm],
                    OUT / "checkpoints.sqlite3")
            except (GatewayError, PlannerError) as error:
                obs = {"run_id": None, "status": "failed",
                       "qc_decision": None, "evidence_validity": None,
                       "needs_human_review": None, "unresolved_pointers": None,
                       "guard_denials": [],
                       "prompt_version": strategies[arm]["prompt_version"],
                       "prompt_sha256": strategies[arm]["prompt_sha256"],
                       "tokens": 0, "requests": 1, "elapsed_s": None,
                       "raw_error": f"planner-level: {error}"[:500],
                       "tool_calls": {}}
                tokens, requests = 0, 1
            grade_info = {}
            if obs.get("qc_decision") is not None:
                report_path = OUT / "runs" / obs["run_id"] / "QC_REPORT.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                grade_info = _grade(case_id, report, f"{case_id}:{arm}")
            rec = {"kind": "observation", "case_id": case_id, "arm": arm,
                   "model_id": MODELS[model_key],
                   "source_group": _source_group(case_id),
                   "at": _now(), **obs, **grade_info}
            guard.settle(tokens, requests,
                         WORST_CASE_TOKENS_PER_RUN, MAX_REQUESTS_PER_RUN)
            ledger_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            ledger_fh.flush()
            completed += 1
            snap = guard.snapshot()
            print(f"[{completed}/120] {label}: {obs['status']} "
                  f"dec={obs['qc_decision']} ev={obs['evidence_validity']} "
                  f"judg={grade_info.get('judgment')} tok={tokens} req={requests} "
                  f"| cum {snap['tokens_spent']}/{S2_TOKEN_CAP} tok, "
                  f"{snap['requests_spent']}/{GLOBAL_REQUEST_CAP} req",
                  flush=True)

    (OUT / "s2_budget_receipt.json").write_text(
        json.dumps({"receipt": "V3_S2_BUDGET_RECEIPT/v1",
                    "finished_at": _now(),
                    "s1_tokens": S1_TOKENS,
                    "s2": guard.snapshot(),
                    "project_total_tokens": S1_TOKENS + guard.tokens,
                    "project_cap": PROJECT_CAP,
                    "aborted_budget": aborted_budget},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"DONE completed={completed}/120 aborted_budget={aborted_budget}",
          flush=True)
    print(f"budget receipt: {OUT / 's2_budget_receipt.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
