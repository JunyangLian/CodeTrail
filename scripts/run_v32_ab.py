"""V3.2 P4: real repeated A/B - 12 frozen hard tasks x 2 models x 2
conditions x 3 repeats = 144 initial observations.

Single-factor controlled experiment (P2.3): BOTH arms use promptv1, no
guards, no refinement - the ONLY changed factor is the context assembly
(A = V3.1 raw-json context, B = ContextBuilder v1). Provider-side sampling
is fixed (temperature=0); top_p/seed are UNSUPPORTED by the gateway and are
labelled as such in the plan.

Resume: (case, model, condition, repeat) cells completed in the ledger are
never re-run; the budget guard is seeded from the ledger. Infra failures:
no auto-retry; one separately-listed bounded repair pass at the end.
"""
from __future__ import annotations

import json
import sys
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
V2_PYTHON = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"
                 r"\bench\inspect_adapter\.venv\Scripts\python.exe")
GRADER_WORKER = ROOT / "scripts" / "eval_grader_worker.py"
# defaults = serial mode; --shard-count N splits the remaining cells across
# N worker processes, each with its OWN ledger + checkpoint db (merged later)
ARGS = None


def _paths() -> tuple[Path, Path, Path, Path]:
    out = ROOT / "data" / "eval" / "v32_ab"
    if ARGS.shard_count > 1:
        ledger = ROOT / f"V3_2_REAL_AB_LEDGER.shard{ARGS.shard_index}.jsonl"
        db = out / f"checkpoints.shard{ARGS.shard_index}.sqlite3"
    else:
        ledger = ROOT / "V3_2_REAL_AB_LEDGER.jsonl"
        db = out / "checkpoints.sqlite3"
    return out, ledger, db, ROOT / "V3_2_REAL_AB_PLAN.json"

CASES = [  # 12 frozen hard tasks, 4 types x 3 families, both golden cases
    "chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
    "chloro_C1_NC_088770_1_CHLORO-NORMAL-00",
    "chloro_C1_NC_140640_1_CHLORO-NORMAL-00",
    "chloro_C1_NC_013991_2_CHLORO-CDS-03",
    "chloro_C1_NC_088771_1_CHLORO-CDS-03",
    "chloro_C1_NC_088765_1_CHLORO-CDS-03",
    "chloro_C1_NC_013991_2_CHLORO-META-05",   # S1 golden (ds/A hallucination)
    "chloro_C1_NC_088757_1_CHLORO-META-05",   # S2 golden (glm/A hallucination)
    "chloro_C1_NC_088762_1_CHLORO-META-05",
    "chloro_C1_NC_082429_1_CHLORO-ID-01",
    "chloro_C1_NC_088771_1_CHLORO-ID-01",
    "chloro_C1_NC_088770_1_CHLORO-ID-01",
]
MODELS = {"glm": "zai-org/GLM-5.3-Flash", "ds": "deepseek-ai/DeepSeek-V4.1-Flash"}
REPEATS = 3
MAX_REQUESTS_PER_RUN = 5
WORST_CASE_TOKENS = 51_000
CIRCUIT_TOKEN_CAP = 5_000_000   # sanity circuit breaker (user: no cap)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _grade(case_id: str, report: dict, cell: str) -> dict:
    mapped = map_report(report)
    if not mapped["mappable"]:
        return {"mappable": False, "skip_reason": mapped["reason"]}
    cell_dir = OUT / "grading" / cell.replace(":", "__")
    cell_dir.mkdir(parents=True, exist_ok=True)
    v2_output = cell_dir / "v2_output.json"
    v2_output.write_text(json.dumps(mapped["output"], ensure_ascii=False, indent=2),
                         encoding="utf-8")
    scorecard_path = cell_dir / "scorecard.json"
    proc = subprocess_run_grader(case_id, v2_output, scorecard_path)
    if proc.returncode != 0:
        return {"mappable": True, "grader_error": proc.stderr[-300:]}
    sc = json.loads(scorecard_path.read_text(encoding="utf-8"))
    return {"mappable": True, "judgment": sc.get("judgment"),
            "verdict_score": sc.get("verdict", {}).get("score"),
            "output_contract_ok": sc.get("output_contract", {}).get("score") == 1,
            "defect_localization": (sc.get("defect_localization")
                                    or sc.get("defect_localisation"))}


def subprocess_run_grader(case_id, v2_output, scorecard_path):
    import subprocess
    return subprocess.run(
        [str(V2_PYTHON), str(GRADER_WORKER), "--case",
         str(V2_CANDIDATES / case_id), "--input", str(v2_output),
         "--out", str(scorecard_path)],
        capture_output=True, text=True, timeout=120, check=False)


def main() -> int:
    import argparse
    global ARGS
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--threads", type=int, default=1)
    ARGS = parser.parse_args()
    import os
    if os.environ.get("GENOMEQC_PAID_AUTHORIZED") != "true":
        raise SystemExit("BLOCKED: caller must set GENOMEQC_PAID_AUTHORIZED=true")
    config.load_dotenv()
    global OUT, LEDGER, PLAN
    OUT, LEDGER, _DB, PLAN = _paths()
    OUT.mkdir(parents=True, exist_ok=True)
    registry = PromptRegistry(STATE / "prompt_registry.v1.json")
    strategy = {"prompt_version": "promptv1",
                "prompt_sha256": registry.get("promptv1")["sha256"],
                "policy_guards": {}}

    if not PLAN.exists():
        PLAN.write_text(json.dumps({
            "plan": "V3_2_REAL_AB_PLAN/v1", "frozen_at": _now(),
            "authorization": "user directive 2026-10-18: V3.2 real API "
                             "allowed, no global token cap, no auto-publish",
            "single_factor": "context assembly ONLY (promptv1 both arms, "
                             "guards/refinement off)",
            "matrix": {"cases": CASES, "models": MODELS,
                       "conditions": {"A": "V3.1 raw-json context",
                                      "B": "ContextBuilder v1"},
                       "repeats": REPEATS,
                       "observations": len(CASES) * 2 * 2 * REPEATS},
            "inference_config": {
                "temperature": 0.0, "top_p": "unsupported (not sent)",
                "max_tokens": 8192,
                "reasoning": "GLM emits reasoning_content (counted once in "
                             "completion); DeepSeek none",
                "seed": "unsupported (not sent)",
                "prompt_sha256": strategy["prompt_sha256"],
                "context_builder_version": "contextv1 (arm B only)",
                "tool_schema": "V3 frozen tool contract v1",
                "scorer": "organelle_v2.rubric.deterministic_grader/v1",
                "dataset": "V2 candidates DEV slice (cases above)"},
            "per_run_limits": {"max_model_requests": MAX_REQUESTS_PER_RUN,
                               "gateway_timeout_s": 120},
            "resume": "ledger (case, model_key, condition, repeat) across "
                      "main + shard ledgers",
            "repair": "separately listed bounded pass, never overwriting",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"frozen plan: {PLAN}", flush=True)

    all_cells = [(c, m, cond, rep) for rep in range(1, REPEATS + 1)
                 for c in CASES for m in MODELS for cond in ("A", "B")]
    cells = [c for i, c in enumerate(all_cells)
             if i % ARGS.shard_count == ARGS.shard_index]
    done = set()
    for seed_path in (ROOT / "V3_2_REAL_AB_LEDGER.jsonl", LEDGER):
        if not seed_path.exists():
            continue
        for line in seed_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("kind") in ("observation", "repair"):
                done.add((rec["case_id"], rec["model_key"],
                          rec["condition"], rec["repeat"]))
    todo = [c for c in cells if c not in done]
    print(f"shard {ARGS.shard_index}/{ARGS.shard_count} threads="
          f"{ARGS.threads}: {len(done)} seeded done, {len(todo)} to run",
          flush=True)

    gateways = {m: ChatGateway(api_key=config.model_env()["api_key"],
                               api_base=config.model_env()["api_base"],
                               model_id=mid, timeout=120.0)
                for m, mid in MODELS.items()}
    builder = ContextBuilder()
    guard = BudgetGuard(token_cap=CIRCUIT_TOKEN_CAP, request_cap=10_000)
    for seed_path in (ROOT / "V3_2_REAL_AB_LEDGER.jsonl", LEDGER):
        seeded = BudgetGuard.from_ledger(seed_path, token_cap=0, request_cap=0)
        guard.tokens += seeded.tokens
        guard.requests += seeded.requests

    import threading
    ledger_lock = threading.Lock()
    progress = {"done": 0}

    def run_one(case_id, model_key, cond, repeat, thread_id, planner_map):
        cell_strategy = {**strategy,
                         "features": {"context_builder_v1": cond == "B"}}
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        try:
            graph = QCGraph(db_path=OUT / f"cp.s{ARGS.shard_index}"
                            f".t{thread_id}.sqlite3",
                            out_dir=OUT / "runs",
                            planner=planner_map[(model_key, cond)])
            try:
                state = graph.run(run_id, case_id,
                                  str(V2_CANDIDATES / case_id),
                                  max_model_requests=MAX_REQUESTS_PER_RUN,
                                  strategy=cell_strategy)
            except (GatewayError, PlannerError) as error:
                state = {"runtime": {"status": "failed", "model_usage": [],
                                     "last_error": str(error)[:500]},
                         "result": {}, "report": {}, "tool_trace": {}}
            finally:
                graph.close()
        finally:
            pass
        elapsed = round(time.monotonic() - started, 1)
        usage = (state.get("runtime") or {}).get("model_usage") or []
        tokens = sum(int(u.get("total_tokens") or 0) for u in usage)
        requests = len(usage)
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
            "prompt_version": strategy["prompt_version"],
            "prompt_sha256": strategy["prompt_sha256"],
            "context": (usage[-1].get("context") if usage
                        and isinstance(usage[-1], dict) else None),
            "tokens": tokens, "requests": requests, "elapsed_s": elapsed,
            "raw_error": ((state.get("runtime") or {})
                          .get("last_error") or "")[:400],
            "tool_calls": {k: len(v) for k, v in
                           (state.get("tool_trace") or {}).items()},
            **grade_info,
        }
        guard.settle(tokens, requests, 0, 0)
        with ledger_lock:
            with LEDGER.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
            progress["done"] += 1
            print(f"[{len(done) + progress['done']}/{len(cells)}] "
                  f"{case_id.split('CHLORO-')[-1]} {model_key}/{cond}"
                  f"/r{repeat}: {rec['status']} dec={rec['qc_decision']} "
                  f"ev={rec['evidence_validity']} judg={rec.get('judgment')} "
                  f"ctx={(rec['context'] or {}).get('completeness', '-')} "
                  f"tok={tokens} req={requests}", flush=True)

    # thread pool over the shard's remaining cells
    todo = [c for c in cells if c not in done]
    chunks = [todo[i::ARGS.threads] for i in range(ARGS.threads)]

    def thread_worker(thread_id: int, chunk: list[tuple]) -> None:
        planner_map = {(m, cond): LLMPlanner(
            gateways[m], max_tokens=8192, prompt_registry=registry,
            prompt_version="promptv1",
            context_builder=builder if cond == "B" else None)
            for m in MODELS for cond in ("A", "B")}
        for cell in chunk:
            run_one(cell[0], cell[1], cell[2], cell[3], thread_id,
                    planner_map)

    threads = [threading.Thread(target=thread_worker,
                                args=(i, chunk), daemon=True)
               for i, chunk in enumerate(chunks) if chunk]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # bounded repair pass (separately listed; never overwrites originals)
    rows = [json.loads(l) for l in
            LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    failed = [r for r in rows if r.get("kind") == "observation"
              and r.get("status") == "failed"]
    repaired_orig = {r.get("repairs_original") for r in rows
                     if r.get("kind") == "repair"}
    for r in failed:
        if r["run_id"] in repaired_orig:
            continue
        print(f"[REPAIR] {r['case_id']} {r['model_key']}/{r['condition']}"
              f"/r{r['repeat']}", flush=True)
        repair = run_one(r["case_id"], r["model_key"], r["condition"],
                         r["repeat"], 0, {(m, cond): LLMPlanner(
                             gateways[m], max_tokens=8192,
                             prompt_registry=registry,
                             prompt_version="promptv1",
                             context_builder=builder if cond == "B" else None)
                             for m in MODELS for cond in ("A", "B")})
        repair = {**repair, "kind": "repair",
                  "repairs_original": r["run_id"]}
        with ledger_lock, LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(repair, ensure_ascii=False) + "\n")
            fh.flush()
        print(f"  -> {repair['status']} dec={repair['qc_decision']}",
              flush=True)

    print(f"DONE shard {ARGS.shard_index}: "
          f"{len(done) + progress['done']}/{len(cells)} cells", flush=True)
    return 0


planners_t = {}  # set per thread in thread_worker


if __name__ == "__main__":
    raise SystemExit(main())
