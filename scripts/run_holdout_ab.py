"""Final-round P3.4: Independent Holdout real evaluation.

24 frozen NEW-source tasks x 2 models x 2 conditions x 1 repeat = 96
initial observations. promptv1 both arms, tools/scorer/budgets identical to
the V3.2 P4 experiment; the only factor is context assembly (A raw-json,
B ContextBuilder v1).

Frozen BEFORE any run:
  - task list + hashes: data/eval/final_round/holdout_dataset/DATASET_MANIFEST.json
  - plan: V3_2_HOLDOUT_PLAN.json (this script writes it once, then honours it)
  - condition order balanced across cases: even case index runs A first,
    odd case index runs B first (fixes the 24/24 A-first ordering bias the
    P1 audit disclosed for V3.2)

Denominator rules (frozen): every launched observation is reported; infra
failures get ONE separately-listed bounded repair and the original stays in
the ledger; no post-hoc strategy adjustment after seeing results.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent import config
from genomeqc_agent.context.builder import ContextBuilder
from genomeqc_agent.eval.adapter import map_report
from genomeqc_agent.evolution.budget import BudgetGuard
from genomeqc_agent.evolution.prompts import PromptRegistry
from genomeqc_agent.model.gateway import ChatGateway, GatewayError
from genomeqc_agent.model.llm_planner import LLMPlanner, PlannerError
from genomeqc_agent.runtime.graph import QCGraph

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "evolution_state"
V2_CANDIDATES = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"
                     ) / "data" / "organelle_v2" / "candidates"
# holdout cases live in the V3 workspace, not the V2 frozen pool
HOLDOUT_CASES = ROOT / "data" / "eval" / "final_round" / "holdout_candidates"
V2_PYTHON = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"
                 r"\bench\inspect_adapter\.venv\Scripts\python.exe")
GRADER_WORKER = ROOT / "scripts" / "eval_grader_worker.py"
OUT = ROOT / "data" / "eval" / "final_round" / "holdout_run"
LEDGER = OUT / "holdout_ledger.jsonl"
PLAN = ROOT / "V3_2_HOLDOUT_PLAN.json"
MANIFEST = ROOT / "data" / "eval" / "final_round" / "holdout_dataset" / \
    "DATASET_MANIFEST.json"

MODELS = {"glm": "zai-org/GLM-5.3-Flash", "ds": "deepseek-ai/DeepSeek-V4.1-Flash"}
MAX_REQUESTS_PER_RUN = 5
CIRCUIT_TOKEN_CAP = 5_000_000
THREADS = 4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _grade(case_id: str, report: dict, cell: str) -> dict:
    mapped = map_report(report)
    if not mapped["mappable"]:
        return {"mappable": False, "skip_reason": mapped["reason"]}
    cell_dir = OUT / "grading" / cell.replace(":", "__")
    cell_dir.mkdir(parents=True, exist_ok=True)
    v2_output = cell_dir / "v2_output.json"
    v2_output.write_text(json.dumps(mapped["output"], ensure_ascii=False,
                                    indent=2), encoding="utf-8")
    scorecard_path = cell_dir / "scorecard.json"
    import subprocess
    proc = subprocess.run(
        [str(V2_PYTHON), str(GRADER_WORKER), "--case",
         str(V2_CANDIDATES / case_id) if (V2_CANDIDATES / case_id).exists()
         else str(HOLDOUT_CASES / case_id),
         "--input", str(v2_output), "--out", str(scorecard_path)],
        capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        return {"mappable": True, "grader_error": proc.stderr[-300:]}
    sc = json.loads(scorecard_path.read_text(encoding="utf-8"))
    return {"mappable": True, "judgment": sc.get("judgment"),
            "verdict_score": sc.get("verdict", {}).get("score"),
            "verdict_delivered": (sc.get("verdict") or {}).get("delivered"),
            "output_contract_ok": sc.get("output_contract", {}).get("score") == 1,
            "defect_localization": (sc.get("defect_localization")
                                    or sc.get("defect_localisation")),
            "root_cause_delivered": (sc.get("root_cause") or {}).get("delivered"),
            "root_cause_expected": (sc.get("root_cause") or {}).get("expected"),
            "critical_failure": (sc.get("critical_failure") or {}).get("flag")}


def verify_manifest() -> list[dict]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for task in manifest["tasks"]:
        case_dir = ROOT / task["case_dir"]
        for rel, h in task["public_sha256"].items():
            actual = _sha((case_dir / rel).read_bytes())
            if actual != h:
                raise SystemExit(f"HASH MISMATCH {task['task_id']}:{rel}")
    print(f"manifest verified: {len(manifest['tasks'])} tasks, all public "
          f"hashes match", flush=True)
    return manifest["tasks"]


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=THREADS)
    args = parser.parse_args()
    import os
    if os.environ.get("GENOMEQC_PAID_AUTHORIZED") != "true":
        raise SystemExit("BLOCKED: caller must set GENOMEQC_PAID_AUTHORIZED=true")
    config.load_dotenv()
    OUT.mkdir(parents=True, exist_ok=True)
    tasks = verify_manifest()
    cases = [t["task_id"] for t in tasks]

    registry = PromptRegistry(STATE / "prompt_registry.v1.json")
    strategy = {"prompt_version": "promptv1",
                "prompt_sha256": registry.get("promptv1")["sha256"],
                "policy_guards": {}}

    if not PLAN.exists():
        PLAN.write_text(json.dumps({
            "plan": "V3_2_HOLDOUT_PLAN/v1", "frozen_at": _now(),
            "authorization": "user directive 2026-10-19: real API allowed "
                             "for the final round; no auto-publish",
            "dataset": "data/eval/final_round/holdout_dataset/"
                       "DATASET_MANIFEST.json (8 NEW source groups x 3 tasks, "
                       "isolation checks all PASS)",
            "single_factor": "context assembly ONLY (promptv1 both arms)",
            "matrix": {"cases": cases, "models": MODELS,
                       "conditions": {"A": "V3.1 raw-json context",
                                      "B": "ContextBuilder v1"},
                       "repeats": 1, "observations": len(cases) * 4},
            "inference_config": {
                "temperature": 0.0, "top_p": "unsupported (not sent)",
                "max_tokens": 8192,
                "prompt_sha256": strategy["prompt_sha256"],
                "context_builder_version": "contextv1 (arm B only)",
                "tool_schema": "V3 frozen tool contract v1",
                "scorer": "organelle_v2.rubric.deterministic_grader/v1"},
            "per_run_limits": {"max_model_requests": MAX_REQUESTS_PER_RUN,
                               "gateway_timeout_s": 120},
            "execution_order": "even case index: A then B; odd case index: "
                               "B then A (balance fix vs V3.2 24/24 A-first)",
            "denominator_rules": "every launched observation reported; infra "
                                 "failures get one separately-listed repair, "
                                 "original preserved; no post-hoc strategy "
                                 "adjustment after results",
            "metrics_frozen": ["qc_verdict_correct",
                               "output_contract_ok", "judgment_pass",
                               "defect_localization_score",
                               "invalid_pointer_rate", "critical_false_pass",
                               "tool_request_efficiency",
                               "context_receipt_completeness",
                               "tokens", "latency_s"],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"frozen plan: {PLAN}", flush=True)

    # cells: per case, condition order balanced by case index
    cells = []
    for i, case in enumerate(cases):
        order = ("A", "B") if i % 2 == 0 else ("B", "A")
        for model in MODELS:
            for cond in order:
                cells.append((case, model, cond, 1))
    done = set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add((rec["case_id"], rec["model_key"],
                          rec["condition"], rec["repeat"]))
    todo = [c for c in cells if c not in done]
    print(f"{len(done)} done, {len(todo)} to run", flush=True)

    gateways = {m: ChatGateway(api_key=config.model_env()["api_key"],
                               api_base=config.model_env()["api_base"],
                               model_id=mid, timeout=120.0)
                for m, mid in MODELS.items()}
    builder = ContextBuilder()
    guard = BudgetGuard(token_cap=CIRCUIT_TOKEN_CAP, request_cap=10_000)
    seeded = BudgetGuard.from_ledger(LEDGER, token_cap=0, request_cap=0)
    guard.tokens += seeded.tokens
    guard.requests += seeded.requests

    ledger_lock = threading.Lock()
    progress = {"done": 0}

    def run_one(case_id, model_key, cond, repeat, thread_id):
        cell_strategy = {**strategy,
                         "features": {"context_builder_v1": cond == "B"}}
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        planner = LLMPlanner(gateways[model_key], max_tokens=8192,
                             prompt_registry=registry, prompt_version="promptv1",
                             context_builder=builder if cond == "B" else None)
        graph = QCGraph(db_path=OUT / f"cp.t{thread_id}.sqlite3",
                        out_dir=OUT / "runs", planner=planner)
        try:
            state = graph.run(run_id, case_id, str(HOLDOUT_CASES / case_id),
                              max_model_requests=MAX_REQUESTS_PER_RUN,
                              strategy=cell_strategy)
        except (GatewayError, PlannerError) as error:
            state = {"runtime": {"status": "failed", "model_usage": [],
                                 "last_error": str(error)[:500]},
                     "result": {}, "report": {}, "tool_trace": {}}
        finally:
            graph.close()
        elapsed = round(time.monotonic() - started, 1)
        usage = (state.get("runtime") or {}).get("model_usage") or []
        tokens = sum(int(u.get("total_tokens") or 0) for u in usage)
        report = state.get("report") or {}
        grade_info: dict = {}
        if report:
            grade_info = _grade(case_id, report,
                                f"{case_id}:{model_key}_{cond}_r{repeat}")
        rec = {
            "kind": "observation", "case_id": case_id,
            "model_key": model_key, "model_id": MODELS[model_key],
            "condition": cond, "repeat": repeat, "run_id": run_id,
            "at": _now(), "status": (state.get("runtime") or {}).get("status"),
            "qc_decision": (state.get("result") or {}).get("qc_decision"),
            "evidence_validity": (state.get("result") or {})
            .get("evidence_validity"),
            "unresolved_pointers": len((state.get("evidence") or {})
                                       .get("unresolved", [])),
            "needs_human_review": report.get("needs_human_review"),
            "prompt_version": "promptv1",
            "prompt_sha256": strategy["prompt_sha256"],
            "context": (usage[-1].get("context") if usage
                        and isinstance(usage[-1], dict) else None),
            "tokens": tokens, "requests": len(usage), "elapsed_s": elapsed,
            "raw_error": ((state.get("runtime") or {})
                          .get("last_error") or "")[:400],
            "tool_calls": {k: len(v) for k, v in
                           (state.get("tool_trace") or {}).items()},
            **grade_info,
        }
        guard.settle(tokens, len(usage), 0, 0)
        with ledger_lock:
            with LEDGER.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
            progress["done"] += 1
            print(f"[{len(done) + progress['done']}/{len(todo)}] "
                  f"{case_id.split('CHLORO-')[-1]} {model_key}/{cond}: "
                  f"{rec['status']} dec={rec['qc_decision']} "
                  f"ev={rec['evidence_validity']} judg={rec.get('judgment')} "
                  f"tok={tokens}", flush=True)

    def thread_worker(thread_id: int, chunk: list) -> None:
        for cell in chunk:
            run_one(*cell, thread_id)

    chunks = [todo[i::args.threads] for i in range(args.threads)]
    threads = [threading.Thread(target=thread_worker, args=(i, chunk),
                                daemon=True)
               for i, chunk in enumerate(chunks) if chunk]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # bounded repair pass (separately listed; originals preserved)
    rows = [json.loads(l) for l in
            LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    failed = [r for r in rows if r.get("kind") == "observation"
              and r.get("status") == "failed"]
    repaired = {r.get("repairs_original") for r in rows
                if r.get("kind") == "repair"}
    for r in failed:
        if r["run_id"] in repaired:
            continue
        print(f"[REPAIR] {r['case_id']} {r['model_key']}/{r['condition']}",
              flush=True)
        planner = LLMPlanner(gateways[r["model_key"]], max_tokens=8192,
                             prompt_registry=registry, prompt_version="promptv1",
                             context_builder=builder
                             if r["condition"] == "B" else None)
        graph = QCGraph(db_path=OUT / "cp.t0.sqlite3", out_dir=OUT / "runs",
                        planner=planner)
        try:
            state = graph.run(str(uuid.uuid4()), r["case_id"],
                              str(HOLDOUT_CASES / r["case_id"]),
                              max_model_requests=MAX_REQUESTS_PER_RUN,
                              strategy={**strategy, "features": {
                                  "context_builder_v1":
                                      r["condition"] == "B"}})
        except (GatewayError, PlannerError) as error:
            state = {"runtime": {"status": "failed", "model_usage": [],
                                 "last_error": str(error)[:500]},
                     "result": {}, "report": {}, "tool_trace": {}}
        finally:
            graph.close()
        usage = (state.get("runtime") or {}).get("model_usage") or []
        repair = {"kind": "repair", "repairs_original": r["run_id"],
                  "case_id": r["case_id"], "model_key": r["model_key"],
                  "condition": r["condition"], "repeat": r["repeat"],
                  "at": _now(),
                  "status": (state.get("runtime") or {}).get("status"),
                  "qc_decision": (state.get("result") or {}).get("qc_decision"),
                  "evidence_validity": (state.get("result") or {})
                  .get("evidence_validity"),
                  "unresolved_pointers": len((state.get("evidence") or {})
                                             .get("unresolved", [])),
                  "tokens": sum(int(u.get("total_tokens") or 0) for u in usage),
                  "run_id": "repair-" + str(uuid.uuid4())}
        with ledger_lock, LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(repair, ensure_ascii=False) + "\n")
            fh.flush()
        print(f"  -> {repair['status']} dec={repair['qc_decision']}",
              flush=True)
    print("HOLDOUT RUN DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
