"""LangGraph QC runtime (plan §4.1 graph, G2).

Nodes: intake_validate -> build_public_packet -> mandatory_checks ->
plan_next_action -> policy_gate -> execute_tool -> verify_observation ->
(bounded_recovery | plan_next_action | synthesize_report | handle_failure)
-> report_schema_validate -> persist_and_finish.

All decisive facts come from tools; the planner only chooses the next
allowed action and explains it. State is JSON-serializable so the SQLite
checkpointer can resume any run by thread_id = run_id.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from genomeqc_agent.jobs.smoke_pipeline import default_registry
from genomeqc_agent.model.llm_planner import PlannerError
from genomeqc_agent.model.scripted_planner import PlannedAction, ScriptedPlanner
from genomeqc_agent.reports.composer import compose_report, render_markdown
from genomeqc_agent.runtime.policy import MAX_VIOLATIONS, evaluate, retries_left, strategy_guards
from genomeqc_agent.runtime.state import TERMINAL_STATUSES, QCState
from genomeqc_agent.schemas.report import QCReport
from genomeqc_agent.schemas.tool import ToolRequest
from genomeqc_agent.tools.registry import ToolRegistry


def _runtime(state: QCState) -> dict:
    return state.get("runtime") or {}


def _budgets(state: QCState) -> dict:
    return state.get("budgets") or {}


def _trace(state: QCState) -> dict:
    return state.get("tool_trace") or {}


def _event(state: QCState, node: str, summary: str) -> dict:
    runtime = _runtime(state)
    return {"step": runtime.get("step", 0), "node": node, "summary": summary}


class QCGraph:
    """Builds and runs the QC StateGraph. One instance per service; each run
    uses a unique thread_id (= run_id) in the checkpointer."""

    def __init__(self, registry: ToolRegistry | None = None,
                 planner: ScriptedPlanner | None = None,
                 db_path: str | Path | None = None,
                 out_dir: str | Path | None = None,
                 interrupt_before: list[str] | None = None):
        self.registry = registry or default_registry()
        self.planner = planner or ScriptedPlanner()
        self.out_dir = Path(out_dir) if out_dir else None
        self._conn = sqlite3.connect(str(db_path) if db_path else ":memory:",
                                     check_same_thread=False)
        self.checkpointer = SqliteSaver(self._conn)
        self.graph = self._build(interrupt_before)

    # -- helpers ----------------------------------------------------------
    def _call(self, state: QCState, tool_name: str, arguments: dict,
              base_trace: dict | None = None,
              base_budgets: dict | None = None) -> tuple:
        request = ToolRequest(run_id=state["run_id"], tool_name=tool_name,
                              arguments=arguments)
        result = self.registry.execute(request, state["input"]["packet_id"])
        # within one node several calls may run sequentially; each must build
        # on the running counters, not on the stale state snapshot
        budgets = dict(base_budgets if base_budgets is not None else _budgets(state))
        if result.ok:
            budgets["tool_calls"] = budgets.get("tool_calls", 0) + 1
        trace = json.loads(json.dumps(base_trace if base_trace is not None
                                      else _trace(state)))  # deep copy
        trace.setdefault(tool_name, []).append(arguments)
        return result, budgets, trace

    def _finish(self, state: QCState, status: str, summary: str) -> dict:
        runtime = dict(_runtime(state))
        runtime["status"] = status
        runtime["step"] = runtime.get("step", 0) + 1
        events = list(state.get("events", [])) + [
            {"step": runtime["step"], "node": "finish", "summary": summary}]
        return {"runtime": runtime, "events": events}

    # -- nodes -------------------------------------------------------------
    def node_intake(self, state: QCState) -> dict:
        payload = state.get("input") or {}
        if not payload.get("packet_id") or not payload.get("packet_dir"):
            return self._fail(state, "intake rejected: packet_id/packet_dir missing")
        runtime = dict(_runtime(state))
        runtime.update(status="running", step=runtime.get("step", 0) + 1,
                       model_id=self.planner.model_id, violations=0,
                       retry_counters={})
        events = list(state.get("events", [])) + [
            {"step": runtime["step"], "node": "intake_validate",
             "summary": f"packet {payload['packet_id']} domain={payload.get('domain')}"}]
        return {"runtime": runtime, "events": events}

    def node_build_packet(self, state: QCState) -> dict:
        payload = state.get("input") or {}
        if _runtime(state).get("status") == "failed":
            return {}
        try:
            self.registry.register_packet(payload["packet_id"], payload["packet_dir"])
        except ValueError:
            pass  # idempotent re-registration (resume/repeat submission)
        files = self.registry.files_for(payload["packet_id"])
        manifest = files.manifest()
        runtime = dict(_runtime(state))
        runtime["step"] = runtime.get("step", 0) + 1
        events = list(state.get("events", [])) + [
            {"step": runtime["step"], "node": "build_public_packet",
             "summary": f"public view opened: {len(manifest)} files"}]
        return {"runtime": runtime, "events": events}

    def node_mandatory(self, state: QCState) -> dict:
        """Mandatory deterministic checks - the planner cannot skip them.

        Bounded recovery applies here too: a retryable failure is retried up
        to max_retries_per_tool inside this node; anything else is fatal.
        """
        if _runtime(state).get("status") == "failed":
            return {}
        budgets = dict(_budgets(state))
        trace = json.loads(json.dumps(_trace(state)))
        counters = dict(_runtime(state).get("retry_counters", {}))
        runtime = dict(_runtime(state))
        events = list(state.get("events", []))
        limit = int(budgets.get("max_retries_per_tool", 1))
        facts = None
        for tool_name, arguments in (("inspect_packet", {}),
                                     ("organelle_summary", {})):
            attempts = 0
            while True:
                result, budgets, trace = self._call(state, tool_name, arguments,
                                                    base_trace=trace,
                                                    base_budgets=budgets)
                if result.ok:
                    break
                error = result.error
                if error.retryable and attempts < limit:
                    attempts += 1
                    counters[tool_name] = counters.get(tool_name, 0) + 1
                    runtime["step"] = runtime.get("step", 0) + 1
                    events.append({"step": runtime["step"], "node": "bounded_recovery",
                                   "summary": f"retry {attempts} of {tool_name} "
                                              f"(mandatory) after: {error.reason}"})
                    continue
                runtime["retry_counters"] = counters
                updates = self._fail(state, f"mandatory check failed: "
                                     f"{error.error_code}: {error.reason}")
                updates["runtime"]["retry_counters"] = counters
                updates["budgets"] = budgets
                return updates
            if tool_name == "organelle_summary":
                facts = result.result
        runtime["step"] = runtime.get("step", 0) + 1
        runtime["retry_counters"] = counters
        events.append({"step": runtime["step"], "node": "deterministic_mandatory_checks",
                       "summary": "inspect_packet + organelle_summary completed (not skippable)"})
        return {"facts": facts, "runtime": runtime, "budgets": budgets,
                "tool_trace": trace, "events": events}

    def node_plan(self, state: QCState) -> dict:
        if _runtime(state).get("status") in TERMINAL_STATUSES:
            return {}
        budgets = dict(_budgets(state))
        # per-run model budget (state-carried, checkpoint-safe); the planner
        # instance is shared across runs, so its own counter must not gate here
        if budgets.get("model_requests", 0) >= int(budgets.get("max_model_requests", 0)):
            return self._budget_stop(state, "model budget exhausted: model_requests")
        try:
            action: PlannedAction = self.planner.next_action(
                state.get("facts"),
                {"tool_trace": _trace(state),
                 "denial": _runtime(state).get("denial")})
        except PlannerError as error:
            # paid calls are never auto-retried (master plan §6): one failed
            # plan is a recorded terminal outcome, not a loop
            updates = self._fail(state, f"planner failed (no paid retry): {error}")
            usage = getattr(self.planner, "last_usage", None)
            if usage:
                runtime = dict(updates["runtime"])
                runtime["model_usage"] = list(_runtime(state).get("model_usage") or []) + [usage]
                updates["runtime"] = runtime
            return updates
        budgets["model_requests"] = budgets.get("model_requests", 0) + 1
        runtime = dict(_runtime(state))
        usage = getattr(self.planner, "last_usage", None)
        if usage:
            runtime["model_usage"] = list(_runtime(state).get("model_usage") or []) + [usage]
        runtime["step"] = runtime.get("step", 0) + 1
        runtime["denial"] = None  # denial consumed by this replan
        events = list(state.get("events", [])) + [
            {"step": runtime["step"], "node": "plan_next_action",
             "summary": f"{action.kind}"
                        + (f": {action.tool_name} ({action.reason})" if action.reason
                           else "")}]
        return {"planned": action.model_dump(), "runtime": runtime,
                "budgets": budgets, "events": events}

    def node_policy(self, state: QCState) -> dict:
        guards = strategy_guards(state)
        allowed_paths: set[str] | None = None
        if guards.get("evidence_enforce_manifest"):
            try:
                files = self.registry.files_for(state["input"]["packet_id"])
                allowed_paths = {entry["path"] for entry in files.manifest()}
            except KeyError:
                allowed_paths = set()  # no manifest -> nothing is citable
        verdict = evaluate(state, self.registry.tool_names(),
                           allowed_paths=allowed_paths, guards=guards)
        runtime = dict(_runtime(state))
        runtime["step"] = runtime.get("step", 0) + 1
        planned = dict(state.get("planned") or {})
        if verdict["allowed"]:
            events = list(state.get("events", [])) + [
                {"step": runtime["step"], "node": "policy_gate",
                 "summary": f"allowed: {planned.get('tool_name') or planned.get('kind')}"}]
            updates: dict = {"runtime": runtime, "events": events}
            if planned.get("kind") == "tool":
                updates["pending_call"] = {"tool_name": planned.get("tool_name"),
                                           "arguments": planned.get("arguments", {})}
            return updates
        runtime["violations"] = runtime.get("violations", 0) + 1
        runtime["denial"] = verdict
        events = list(state.get("events", [])) + [
            {"step": runtime["step"], "node": "policy_gate",
             "summary": f"denied: {verdict['code']} ({verdict['reason']})"}]
        # clear any stale pending call so routing sees the denial, not the
        # previous successful execution
        return {"runtime": runtime, "events": events, "pending_call": None}

    def node_violation(self, state: QCState) -> dict:
        if _runtime(state).get("violations", 0) > MAX_VIOLATIONS:
            return self._fail(state, "policy denied repeatedly; stopping")
        # one replan chance: the planner sees the denial via runtime.denial
        return {}

    def node_execute(self, state: QCState) -> dict:
        pending = state.get("pending_call") or {}
        packet_id = state["input"]["packet_id"]
        try:
            self.registry.files_for(packet_id)
        except KeyError:
            # cross-process resume: the packet sandbox is memory-resident in
            # each process and must be re-opened (idempotent, read-only)
            try:
                self.registry.register_packet(packet_id,
                                              state["input"]["packet_dir"])
            except ValueError:
                pass
        result, budgets, trace = self._call(state, pending["tool_name"],
                                            pending.get("arguments", {}))
        runtime = dict(_runtime(state))
        runtime["step"] = runtime.get("step", 0) + 1
        events = list(state.get("events", [])) + [
            {"step": runtime["step"], "node": "execute_tool",
             "summary": (f"{pending['tool_name']} ok" if result.ok else
                         f"{pending['tool_name']} error: {result.error.error_code}")}]
        return {"last_result": result.model_dump(), "budgets": budgets,
                "tool_trace": trace, "runtime": runtime, "events": events}

    def node_verify(self, state: QCState) -> dict:
        result = state.get("last_result") or {}
        runtime = dict(_runtime(state))
        runtime["step"] = runtime.get("step", 0) + 1
        events = list(state.get("events", []))
        if result.get("ok"):
            tool_name = result["tool_name"]
            updates: dict = {"runtime": runtime}
            if tool_name == "verify_evidence_pointer":
                events.append({"step": runtime["step"], "node": "verify_observation",
                               "summary": "evidence pointer report recorded"})
                updates["evidence"] = result["result"]
            else:
                events.append({"step": runtime["step"], "node": "verify_observation",
                               "summary": f"{tool_name} observation accepted"})
            updates["events"] = events
            return updates  # -> plan_next_action decides sufficiency
        error = result.get("error") or {}
        retries = retries_left(state, result.get("tool_name", ""))
        if error.get("retryable") and retries > 0:
            events.append({"step": runtime["step"], "node": "verify_observation",
                           "summary": f"retriable error ({error['error_code']}); "
                                      f"{retries} retry(ies) left"})
            return {"runtime": runtime, "events": events}  # -> bounded_recovery
        return self._fail(state, f"unrecoverable tool error: "
                          f"{error.get('error_code')}: {error.get('reason')}")

    def node_recovery(self, state: QCState) -> dict:
        result = state.get("last_result") or {}
        tool_name = result.get("tool_name", "")
        runtime = dict(_runtime(state))
        counters = dict(runtime.get("retry_counters", {}))
        counters[tool_name] = counters.get(tool_name, 0) + 1
        runtime["retry_counters"] = counters
        runtime["step"] = runtime.get("step", 0) + 1
        events = list(state.get("events", [])) + [
            {"step": runtime["step"], "node": "bounded_recovery",
             "summary": f"retry {counters[tool_name]} of {tool_name}"}]
        return {"runtime": runtime, "events": events}  # -> execute_tool

    def node_budget_stop(self, state: QCState) -> dict:
        return self._budget_stop(state, "run stopped: budget exhausted")

    def node_fail(self, state: QCState) -> dict:
        return state  # status/summary already set by _fail

    def node_synthesize(self, state: QCState) -> dict:
        if _runtime(state).get("status") in TERMINAL_STATUSES:
            return {}
        evidence = state.get("evidence") or {}
        unresolved = evidence.get("unresolved", [])
        trace = _trace(state)
        strategy = state.get("strategy") or {}
        guards = strategy.get("policy_guards") or {}
        prompt_version = strategy.get("prompt_version",
                                      getattr(self.planner, "prompt_version", "unknown"))
        prompt_sha = strategy.get("prompt_sha256",
                                  getattr(self.planner, "prompt_sha256", ""))
        report = compose_report(
            run_id=state["run_id"],
            packet_meta=state["input"],
            domain=state["input"].get("domain", "auto_detect_supported"),
            model_id=self.planner.model_id,
            tool_results=[],  # deterministic composer reads facts/evidence directly
            facts=state.get("facts"),
            unresolved_count=len(unresolved),
            tool_calls={name: len(calls) for name, calls in trace.items()},
            unresolved_requires_review=bool(guards.get("unresolved_requires_review")),
            versions={"planner": self.planner.model_id,
                      "prompt": str(prompt_version),
                      "prompt_sha256": str(prompt_sha),
                      "policy_guards": ",".join(
                          sorted(k for k, v in guards.items() if v)) or "none",
                      "graph": "qc-stategraph-v1"})
        report_dict = report.model_dump()
        runtime = dict(_runtime(state))
        runtime["step"] = runtime.get("step", 0) + 1
        events = list(state.get("events", [])) + [
            {"step": runtime["step"], "node": "synthesize_report",
             "summary": f"decision={report_dict['qc_decision']} "
                        f"evidence={report_dict['evidence_validity']}"}]
        return {"report": report_dict, "runtime": runtime, "events": events}

    def node_validate_report(self, state: QCState) -> dict:
        report = state.get("report") or {}
        runtime = dict(_runtime(state))
        runtime["step"] = runtime.get("step", 0) + 1
        events = list(state.get("events", [])) + [
            {"step": runtime["step"], "node": "report_schema_validate",
             "summary": f"findings={len(report.get('findings', []))} schema-valid"}]
        return {"runtime": runtime, "events": events}

    def node_persist(self, state: QCState) -> dict:
        report = state.get("report") or {}
        status = _runtime(state).get("status", "succeeded")
        if status not in TERMINAL_STATUSES:
            status = "succeeded"  # decision itself lives in result.qc_decision
        result_block = {
            "qc_decision": report.get("qc_decision"),
            "evidence_validity": report.get("evidence_validity"),
            "report_ref": None,
        }
        summary = f"persisted decision={result_block['qc_decision']}"
        if self.out_dir is not None and report:
            run_dir = self.out_dir / state["run_id"]
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "QC_REPORT.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (run_dir / "QC_REPORT.md").write_text(
                render_markdown(QCReport(**report)), encoding="utf-8")
            result_block["report_ref"] = str(run_dir / "QC_REPORT.json")
            summary = f"persisted report to {run_dir}"
        return {**self._finish(state, status, summary), "result": result_block}

    # -- failure helpers ---------------------------------------------------
    def _fail(self, state: QCState, summary: str) -> dict:
        runtime = dict(_runtime(state))
        runtime["status"] = "failed"
        runtime["step"] = runtime.get("step", 0) + 1
        runtime["last_error"] = summary
        events = list(state.get("events", [])) + [
            {"step": runtime["step"], "node": "handle_failure", "summary": summary}]
        return {"runtime": runtime, "events": events}

    def _budget_stop(self, state: QCState, summary: str) -> dict:
        updates = self._finish(state, "budget_exhausted", summary)
        updates["planned"] = None
        # terminal state always carries a result block, even without a report
        updates["result"] = {"qc_decision": None,
                             "evidence_validity": "not_applicable",
                             "report_ref": None}
        return updates

    # -- routing -----------------------------------------------------------
    def route_after_plan(self, state: QCState) -> str:
        if _runtime(state).get("status") in TERMINAL_STATUSES:
            return "stop"
        if (state.get("planned") or {}).get("kind") == "tool":
            return "policy"
        # synthesize passes the gate only under the pre_verify_pointers guard;
        # with guards off the baseline topology is unchanged
        guards = strategy_guards(state)
        return "policy" if guards.get("pre_verify_pointers") else "synthesize"

    def route_after_policy(self, state: QCState) -> str:
        denial = _runtime(state).get("denial")
        if denial and denial.get("code") == "budget_exhausted":
            return "budget"  # dedicated terminal, not a replan situation
        if denial and not state.get("pending_call"):
            return "violation"
        if (state.get("planned") or {}).get("kind") == "synthesize":
            return "synthesize"
        return "execute"

    def route_after_verify(self, state: QCState) -> str:
        result = state.get("last_result") or {}
        if result.get("ok"):
            return "plan"
        if (result.get("error") or {}).get("retryable"):
            return "recovery"
        return "fail"

    def route_terminal(self, state: QCState) -> str:
        return END

    # -- assembly ----------------------------------------------------------
    def _build(self, interrupt_before: list[str] | None = None) -> CompiledStateGraph:
        builder = StateGraph(QCState)
        builder.add_node("intake_validate", self.node_intake)
        builder.add_node("build_public_packet", self.node_build_packet)
        builder.add_node("mandatory_checks", self.node_mandatory)
        builder.add_node("plan_next_action", self.node_plan)
        builder.add_node("policy_gate", self.node_policy)
        builder.add_node("record_violation", self.node_violation)
        builder.add_node("execute_tool", self.node_execute)
        builder.add_node("verify_observation", self.node_verify)
        builder.add_node("bounded_recovery", self.node_recovery)
        builder.add_node("synthesize_report", self.node_synthesize)
        builder.add_node("report_schema_validate", self.node_validate_report)
        builder.add_node("persist_and_finish", self.node_persist)
        builder.add_node("budget_stop", self.node_budget_stop)
        builder.add_node("handle_failure", self.node_fail)

        builder.add_edge(START, "intake_validate")
        builder.add_conditional_edges(
            "intake_validate",
            lambda s: "build" if _runtime(s).get("status") != "failed" else "fail",
            {"build": "build_public_packet", "fail": "handle_failure"})
        builder.add_edge("build_public_packet", "mandatory_checks")
        builder.add_edge("mandatory_checks", "plan_next_action")
        builder.add_conditional_edges(
            "plan_next_action", self.route_after_plan,
            {"policy": "policy_gate", "synthesize": "synthesize_report",
             "stop": "report_schema_validate"})
        builder.add_conditional_edges(
            "policy_gate", self.route_after_policy,
            {"execute": "execute_tool", "violation": "record_violation",
             "budget": "budget_stop", "synthesize": "synthesize_report"})
        builder.add_conditional_edges(
            "record_violation",
            lambda s: "plan" if _runtime(s).get("violations", 0) <= MAX_VIOLATIONS else "fail",
            {"plan": "plan_next_action", "fail": "handle_failure"})
        builder.add_edge("execute_tool", "verify_observation")
        builder.add_conditional_edges(
            "verify_observation", self.route_after_verify,
            {"plan": "plan_next_action", "recovery": "bounded_recovery",
             "fail": "handle_failure"})
        builder.add_edge("bounded_recovery", "execute_tool")
        builder.add_edge("synthesize_report", "report_schema_validate")
        builder.add_edge("report_schema_validate", "persist_and_finish")
        builder.add_edge("persist_and_finish", END)
        builder.add_edge("handle_failure", END)
        builder.add_edge("budget_stop", END)

        return builder.compile(checkpointer=self.checkpointer,
                               interrupt_before=interrupt_before)

    # -- public API --------------------------------------------------------
    def run(self, run_id: str, packet_id: str, packet_dir: str | Path,
            domain: str = "auto_detect_supported", user_goal: str = "",
            input_sha256: str = "", max_tool_calls: int = 32,
            max_model_requests: int = 8, max_retries_per_tool: int = 1,
            strategy: dict | None = None) -> QCState:
        initial: QCState = {
            "run_id": run_id,
            "input": {"packet_id": packet_id, "packet_dir": str(packet_dir),
                      "domain": domain, "user_goal": user_goal,
                      "input_sha256": input_sha256},
            "runtime": {"status": "queued", "step": 0, "violations": 0,
                        "retry_counters": {}},
            "budgets": {"max_tool_calls": max_tool_calls,
                        "max_model_requests": max_model_requests,
                        "max_retries_per_tool": max_retries_per_tool,
                        "tool_calls": 0, "model_requests": 0},
            # A/B strategy block (Phase 2): absent/empty = baseline strategy.
            # Shadow runs pass the candidate here - the active strategy is
            # never mutated by a run.
            "strategy": dict(strategy or {}),
            "events": [],
        }
        config = {"configurable": {"thread_id": run_id}}
        return self.graph.invoke(initial, config)

    def resume(self, run_id: str, max_rounds: int = 16) -> QCState:
        """Resume a paused/interrupted run from its checkpoint.

        interrupt_before stops before EVERY entry into the target node (the
        graph plans once per evidence round), so resuming is a loop: invoke
        until the run reaches a terminal status or no pending node remains.
        """
        config = {"configurable": {"thread_id": run_id}}
        state = self.graph.get_state(config).values
        for _ in range(max_rounds):
            if not self.graph.get_state(config).next:
                return state
            state = self.graph.invoke(None, config)
            if _runtime(state).get("status") in TERMINAL_STATUSES:
                return state
        return state

    def get_state(self, run_id: str) -> QCState:
        return self.graph.get_state({"configurable": {"thread_id": run_id}}).values

    def close(self) -> None:
        self._conn.close()
