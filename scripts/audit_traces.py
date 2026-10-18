"""G4 trace audit: six-dimension analysis of the real LLM-planned runs.

Reads the LangGraph SQLite checkpoints of the GLM and DeepSeek 40-case
slices and produces the per-dimension evidence used by
docs/V3_AGENT_TRACE_REPORT.md. Offline; touches no model API.

Usage: python scripts/audit_traces.py [--out docs/trace_audit.json]
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

RUNS = {
    "glm-5.3-flash": Path("data/eval/g4_glm_chloro40/eval_checkpoint.sqlite3"),
    "deepseek-v4.1-flash": Path("data/eval/g4_ds_chloro40/eval_checkpoint.sqlite3"),
}


def load_states(db_path: Path) -> list[dict]:
    """Read terminal states through the official API: rebuild the graph shape
    and ask its SqliteSaver-backed checkpointer for each thread's state."""
    import sqlite3

    from genomeqc_agent.runtime.graph import QCGraph

    graph = QCGraph(db_path=db_path)
    conn = sqlite3.connect(str(db_path))
    threads = [r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM checkpoints")]
    conn.close()
    states = []
    for thread_id in threads:
        try:
            values = graph.get_state(thread_id)
            if values:
                states.append(values)
        except Exception as error:  # noqa: BLE001 - audit must survive bad rows
            states.append({"_error": f"{type(error).__name__}: {error}"})
    graph.close()
    return states


def audit_model(label: str, states: list[dict]) -> dict:
    runs = [s for s in states if s.get("run_id") and not s.get("_error")]
    tool_trace_dist: dict[str, int] = {}
    route_counts = {"lookup_branch": 0, "direct_verify": 0}
    tool_calls = 0
    plan_events = plan_tool = plan_synthesize = 0
    recovery_events = 0
    verify_pointers = verify_resolved = 0
    usages = []
    statuses: dict[str, int] = {}

    for values in runs:
        trace = values.get("tool_trace") or {}
        tools_used = tuple(sorted(trace))
        tool_trace_dist[tools_used] = tool_trace_dist.get(tools_used, 0) + 1
        if "lookup_feature" in trace:
            route_counts["lookup_branch"] += 1
        else:
            route_counts["direct_verify"] += 1
        for calls in trace.values():
            tool_calls += len(calls)
        runtime = values.get("runtime") or {}
        statuses[runtime.get("status", "?")] = statuses.get(runtime.get("status", "?"), 0) + 1
        for event in values.get("events", []):
            node = event.get("node")
            if node == "plan_next_action":
                plan_events += 1
                if event.get("summary", "").startswith("tool:"):
                    plan_tool += 1
                elif event.get("summary", "").startswith("synthesize"):
                    plan_synthesize += 1
            elif node in ("bounded_recovery",):
                recovery_events += 1
        evidence = values.get("evidence") or {}
        for pointer in evidence.get("pointers", []):
            verify_pointers += 1
            if pointer.get("resolved"):
                verify_resolved += 1
        for usage in runtime.get("model_usage", []):
            if usage.get("latency_ms") is not None:
                usages.append(usage)

    latencies = [u["latency_ms"] for u in usages if u.get("latency_ms")]
    latencies.sort()
    return {
        "runs": len(runs),
        "statuses": statuses,
        "tool_call_totals": {"calls": tool_calls, "recorded_in_trace": True,
                             "trace_shapes": {"/".join(k) if k else "none": v
                                              for k, v in tool_trace_dist.items()}},
        "routing": {"distinct_routes": len(route_counts), **route_counts},
        "planning": {"plan_events": plan_events, "tool_plans": plan_tool,
                     "synthesize_plans": plan_synthesize,
                     "dynamic": plan_tool > 0 and plan_synthesize > 0},
        "recovery": {"bounded_recovery_events": recovery_events},
        "evidence": {"pointers_cited": verify_pointers,
                     "pointers_resolved": verify_resolved,
                     "resolution_rate": round(verify_resolved / verify_pointers, 4)
                     if verify_pointers else None},
        "cost_latency": {
            "calls": len(usages),
            "total_tokens": sum(u.get("total_tokens") or 0 for u in usages),
            "latency_ms_median": statistics.median(latencies) if latencies else None,
            "latency_ms_p95": latencies[int(len(latencies) * 0.95) - 1] if latencies else None,
        },
        "label": label,
    }


def main(argv=None) -> int:
    out_path = Path(argv[0] if argv else "docs/trace_audit.json")
    report = {label: audit_model(label, load_states(path))
              for label, path in RUNS.items() if path.is_file()}
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps({k: {d: v for d, v in r.items() if d in
                          ("runs", "routing", "planning", "recovery", "evidence",
                           "cost_latency", "statuses")}
                      for k, r in report.items()}, ensure_ascii=False, indent=1))
    print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
