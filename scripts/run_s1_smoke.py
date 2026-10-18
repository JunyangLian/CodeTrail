"""S1 Smoke (FROZEN matrix, user-authorized 2026-10-18, scope = S1 ONLY).

16 task observations = 4 frozen DEV tasks x 2 models x 2 strategies.
Serial, one run per cell, no best-of, no auto-retry inside a run (infra
failures are recorded as failed and counted in the denominator; one
separately-listed repair pass runs at the end if budget remains).

Hard caps (frozen): 150,000 tokens / 80 HTTP requests. The ledger checks
remaining budget BEFORE every observation and stops honestly (remaining
cells are recorded as skipped_due_to_budget, never silently dropped).

Authorization: GENOMEQC_PAID_AUTHORIZED=true must be set by the CALLER.
Key rotation was explicitly waived by the user (free-tier key).
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
from genomeqc_agent.evolution.prompts import PromptRegistry
from genomeqc_agent.model.gateway import ChatGateway, GatewayError
from genomeqc_agent.model.llm_planner import LLMPlanner, PlannerError
from genomeqc_agent.runtime.graph import QCGraph

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "s1_smoke"
STATE = ROOT / "evolution_state"
V2_CANDIDATES = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2"
                     ) / "data" / "organelle_v2" / "candidates"

TOKEN_CAP = 150_000
REQUEST_CAP = 80
MAX_REQUESTS_PER_RUN = 5  # observed mean 2.5 + one guard-denial replan + margin

MODELS = {"glm": "zai-org/GLM-5.3-Flash", "ds": "deepseek-ai/DeepSeek-V4.1-Flash"}
TASKS = [  # frozen DEV tasks (seen cases; smoke = shakedown, never scored)
    ("chloro_C1_NC_013991_2_CHLORO-NORMAL-00",
     str(ROOT / "examples" / "public_packets" / "chloro_C1_NC_013991_2_CHLORO-NORMAL-00")),
    ("chloro_C1_NC_013991_2_CHLORO-CDS-03",
     str(ROOT / "examples" / "public_packets" / "chloro_C1_NC_013991_2_CHLORO-CDS-03")),
    ("chloro_C1_NC_013991_2_CHLORO-META-05",
     str(V2_CANDIDATES / "chloro_C1_NC_013991_2_CHLORO-META-05")),
    ("chloro_C1_NC_082429_1_CHLORO-ID-01",
     str(V2_CANDIDATES / "chloro_C1_NC_082429_1_CHLORO-ID-01")),
]
GUARDS_B = {"evidence_enforce_manifest": True, "pre_verify_pointers": True,
            "unresolved_requires_review": True}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _authorize() -> None:
    import os
    if os.environ.get("GENOMEQC_PAID_AUTHORIZED") != "true":
        raise SystemExit("BLOCKED: GENOMEQC_PAID_AUTHORIZED=true must be set by "
                         "the caller (user signature; the script never "
                         "self-authorizes)")
    if not os.environ.get("GENOMEQC_API_KEY") or not os.environ.get("GENOMEQC_API_BASE"):
        raise SystemExit("BLOCKED: GENOMEQC_API_KEY / GENOMEQC_API_BASE missing")


def _state_usage(state: dict) -> tuple[int, int]:
    """(tokens, requests) actually consumed by one observation."""
    usage = (state.get("runtime") or {}).get("model_usage") or []
    tokens = sum(int(u.get("total_tokens") or 0) for u in usage)
    return tokens, len(usage)


def _run_observation(planner: LLMPlanner, task_id: str, packet_dir: str,
                     strategy: dict, db_path: Path) -> dict:
    run_id = str(uuid.uuid4())
    graph = QCGraph(db_path=db_path, out_dir=OUT / "runs", planner=planner)
    try:
        started = time.monotonic()
        state = graph.run(run_id, task_id, packet_dir,
                          max_model_requests=MAX_REQUESTS_PER_RUN,
                          strategy=strategy)
        elapsed = round(time.monotonic() - started, 1)
    finally:
        graph.close()
    tokens, requests = _state_usage(state)
    runtime = state.get("runtime") or {}
    result = state.get("result") or {}
    report = state.get("report") or {}
    raw_error = runtime.get("last_error") or ""
    events_denials = [e["summary"] for e in state.get("events", [])
                      if e["node"] == "policy_gate"
                      and e["summary"].startswith("denied")]
    return {
        "run_id": run_id, "status": runtime.get("status"),
        "qc_decision": result.get("qc_decision"),
        "evidence_validity": result.get("evidence_validity"),
        "needs_human_review": report.get("needs_human_review"),
        "unresolved_pointers": len((state.get("evidence") or {})
                                   .get("unresolved", [])),
        "guard_denials": events_denials,
        "prompt_version": (report.get("versions") or {}).get("prompt"),
        "prompt_sha256": (report.get("versions") or {}).get("prompt_sha256"),
        "tokens": tokens, "requests": requests, "elapsed_s": elapsed,
        "raw_error": raw_error[:500],
        "tool_calls": {k: len(v) for k, v in (state.get("tool_trace") or {}).items()},
    }


def main() -> int:
    config.load_dotenv()
    _authorize()
    OUT.mkdir(parents=True, exist_ok=True)
    registry = PromptRegistry(STATE / "prompt_registry.v1.json")
    strategies = {
        "A": {"prompt_version": "promptv1",
              "prompt_sha256": registry.get("promptv1")["sha256"],
              "policy_guards": {}},
        "B": {"prompt_version": "promptv2",
              "prompt_sha256": registry.get("promptv2")["sha256"],
              "policy_guards": dict(GUARDS_B)},
    }
    gateways = {m: ChatGateway(api_key=config.model_env()["api_key"],
                               api_base=config.model_env()["api_base"],
                               model_id=mid, timeout=120.0)
                for m, mid in MODELS.items()}
    planners = {(m, v): LLMPlanner(gateways[m], max_tokens=8192,
                                   prompt_registry=registry, prompt_version=v)
                for m in MODELS for v in ("promptv1", "promptv2")}

    ledger_path = OUT / "s1_ledger.jsonl"
    report: dict = {
        "receipt": "S1_SMOKE/v1", "started_at": _now(),
        "authorization": {"authorized_by": "user directive 2026-10-18 (S1 only)",
                          "key_rotation": "waived by user (free-tier key)",
                          "scope": "S1 smoke only; S2 NOT authorized"},
        "caps": {"tokens": TOKEN_CAP, "requests": REQUEST_CAP,
                 "max_requests_per_run": MAX_REQUESTS_PER_RUN},
        "matrix": {"tasks": [t[0] for t in TASKS],
                   "models": MODELS, "strategies": ["A promptv1", "B promptv2+guards"]},
        "features_disabled": ["self_refinement", "data_flywheel"],
        "observations": [], "repair": [], "skipped": [],
    }
    spent_tokens = spent_requests = 0

    for task_id, packet_dir in TASKS:
        for model_key, model_id in MODELS.items():
            for strat_key in ("A", "B"):
                if spent_tokens >= TOKEN_CAP or spent_requests >= REQUEST_CAP:
                    report["skipped"].append({
                        "task": task_id, "model": model_id, "strategy": strat_key,
                        "reason": f"budget cap reached "
                                  f"({spent_tokens}/{TOKEN_CAP} tok, "
                                  f"{spent_requests}/{REQUEST_CAP} req)"})
                    print(f"[SKIP] {task_id} {model_key}/{strat_key}: cap reached")
                    continue
                planner = planners[(model_key, "promptv1" if strat_key == "A"
                                    else "promptv2")]
                label = f"{task_id} {model_key}/{strat_key}"
                try:
                    obs = _run_observation(planner, task_id, packet_dir,
                                           strategies[strat_key],
                                           OUT / "checkpoints.sqlite3")
                except (GatewayError, PlannerError) as error:
                    obs = {"run_id": None, "status": "failed",
                           "qc_decision": None, "evidence_validity": None,
                           "needs_human_review": None,
                           "unresolved_pointers": None, "guard_denials": [],
                           "prompt_version": strategies[strat_key]["prompt_version"],
                           "prompt_sha256": strategies[strat_key]["prompt_sha256"],
                           "tokens": 0, "requests": 1, "elapsed_s": None,
                           "raw_error": f"planner-level: {error}"[:500],
                           "tool_calls": {}}
                obs.update({"task": task_id, "model": model_id,
                            "model_key": model_key, "strategy": strat_key,
                            "at": _now()})
                spent_tokens += obs["tokens"]
                spent_requests += obs["requests"]
                report["observations"].append(obs)
                with ledger_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(obs, ensure_ascii=False) + "\n")
                print(f"[{len(report['observations']) + len(report['skipped'])}/16] "
                      f"{label}: {obs['status']} decision={obs['qc_decision']} "
                      f"tok={obs['tokens']} req={obs['requests']} "
                      f"unresolved={obs['unresolved_pointers']} "
                      f"denials={len(obs['guard_denials'])} "
                      f"err={obs['raw_error'][:60] or '-'}",
                      flush=True)

    # -- repair pass: one bounded rerun per infra-failed observation ---------
    for obs in [o for o in report["observations"]
                if o["status"] == "failed"
                and ("gateway" in (o["raw_error"] or "").lower()
                     or "503" in (o["raw_error"] or ""))]:
        if spent_tokens >= TOKEN_CAP or spent_requests >= REQUEST_CAP:
            break
        planner = planners[(obs["model_key"],
                            "promptv1" if obs["strategy"] == "A" else "promptv2")]
        packet_dir = dict(TASKS)[obs["task"]]
        print(f"[REPAIR] {obs['task']} {obs['model_key']}/{obs['strategy']}",
              flush=True)
        try:
            repair = _run_observation(planner, obs["task"], packet_dir,
                                      strategies[obs["strategy"]],
                                      OUT / "checkpoints.sqlite3")
        except (GatewayError, PlannerError) as error:
            repair = {"status": "failed", "tokens": 0, "requests": 1,
                      "raw_error": str(error)[:500]}
        repair.update({"task": obs["task"], "model": obs["model"],
                       "model_key": obs["model_key"], "strategy": obs["strategy"],
                       "repairs_original": obs["run_id"], "at": _now()})
        spent_tokens += repair.get("tokens", 0)
        spent_requests += repair.get("requests", 0)
        report["repair"].append(repair)
        with ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "repair", **repair}, ensure_ascii=False) + "\n")

    report["finished_at"] = _now()
    report["budget"] = {"tokens_spent": spent_tokens, "requests_spent": spent_requests,
                        "token_cap": TOKEN_CAP, "request_cap": REQUEST_CAP,
                        "within_caps": spent_tokens <= TOKEN_CAP
                        and spent_requests <= REQUEST_CAP}
    report["denominator"] = {
        "observations_planned": 16,
        "observations_executed": len(report["observations"]),
        "observations_skipped_for_budget": len(report["skipped"]),
        "repair_runs": len(report["repair"]),
        "complete": len(report["observations"]) == 16,
    }
    (OUT / "s1_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nbudget: {spent_tokens}/{TOKEN_CAP} tok, "
          f"{spent_requests}/{REQUEST_CAP} req")
    print(f"denominator: {report['denominator']}")
    print(f"report: {OUT / 's1_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
