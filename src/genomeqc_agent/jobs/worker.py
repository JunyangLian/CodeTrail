"""Single-process controlled worker (plan §2): one daemon thread consuming a
FIFO queue; SQLite ledger carries the queue state across restarts.

P4: per-job planner selection. The scripted graph is shared; LLM graphs are
built lazily per (model_id, prompt_version) and ONLY after the API layer
verified paid-run authorisation - the worker itself never authorises.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path

from genomeqc_agent.model.gateway import ChatGateway
from genomeqc_agent.model.llm_planner import LLMPlanner
from genomeqc_agent.runtime.graph import QCGraph
from genomeqc_agent.storage.ledger import TERMINAL, JobStore

logger = logging.getLogger(__name__)

PAID_AUTH_ENV = "GENOMEQC_PAID_AUTHORIZED"


class JobWorker:
    def __init__(self, graph: QCGraph, ledger: JobStore,
                 db_path: str | Path | None = None,
                 out_dir: str | Path | None = None):
        self.graph = graph  # shared scripted graph
        self.ledger = ledger
        self._db_path = db_path
        self._out_dir = out_dir
        self._llm_graphs: dict[tuple[str, str], QCGraph] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._cancelled: set[str] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="genomeqc-worker")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put("")  # wake the loop
        self._thread.join(timeout=5)

    def submit(self, run_id: str, packet_id: str, packet_dir: str, domain: str,
               user_goal: str, budgets: dict, planner_kind: str = "scripted",
               model_id: str | None = None, prompt_version: str = "promptv1",
               strategy: dict | None = None) -> None:
        stored = {**budgets, "planner_kind": planner_kind,
                  "model_id": model_id, "prompt_version": prompt_version,
                  "strategy": strategy or {}}
        self.ledger.create_job(run_id, packet_id, packet_dir, domain, user_goal,
                               stored)
        self._queue.put(run_id)

    def cancel(self, run_id: str) -> str:
        """Queued jobs are cancelled outright; running jobs can only be
        flagged (offline runs are short; hard-killing mid-graph is G3.1)."""
        job = self.ledger.get_job(run_id)
        if job is None:
            return "not_found"
        if job.status in TERMINAL:
            return "already_finished"
        self._cancelled.add(run_id)
        return "cancel_requested"

    def _graph_for(self, planner_kind: str, model_id: str | None,
                   prompt_version: str) -> QCGraph:
        if planner_kind != "llm":
            return self.graph
        key = (model_id or "", prompt_version)
        if key not in self._llm_graphs:
            import os
            assert os.environ.get(PAID_AUTH_ENV) == "true", \
                "paid runs require GENOMEQC_PAID_AUTHORIZED=true (defence in depth)"
            gateway = ChatGateway(api_key=os.environ.get("GENOMEQC_API_KEY", ""),
                                  api_base=os.environ.get("GENOMEQC_API_BASE", ""),
                                  model_id=key[0], timeout=120.0)
            from genomeqc_agent.evolution.prompts import PromptRegistry
            state_dir = Path(self._db_path or "./data").resolve().parent \
                / "evolution_state" / "prompt_registry.v1.json"
            registry = PromptRegistry(state_dir) if state_dir.is_file() else None
            if registry is None and key[1] != "promptv1":
                raise RuntimeError(
                    f"prompt registry not found; refusing to run {key[1]} "
                    "unbound (integrity requires the registry)")
            planner = LLMPlanner(gateway, max_tokens=8192,
                                 prompt_registry=registry,
                                 prompt_version=key[1])
            self._llm_graphs[key] = QCGraph(db_path=self._db_path,
                                            out_dir=self._out_dir,
                                            planner=planner)
        return self._llm_graphs[key]

    def _loop(self) -> None:
        while not self._stop.is_set():
            run_id = self._queue.get()
            if self._stop.is_set() or not run_id:
                continue
            job = self.ledger.get_job(run_id)
            if job is None:
                continue
            if run_id in self._cancelled:
                self._cancelled.discard(run_id)
                self.ledger.mark_cancelled(run_id)
                continue
            self.ledger.mark_running(run_id)
            try:
                stored = json.loads(job.budgets_json or "{}")
                budgets = {k: stored[k] for k in
                           ("max_tool_calls", "max_model_requests",
                            "max_retries_per_tool") if k in stored}
                graph = self._graph_for(stored.get("planner_kind", "scripted"),
                                        stored.get("model_id"),
                                        stored.get("prompt_version", "promptv1"))
                state = graph.run(
                    run_id, job.packet_id, job.packet_dir, domain=job.domain,
                    user_goal=job.user_goal,
                    strategy=stored.get("strategy") or {}, **budgets)
                result = state.get("result") or {}
                runtime = state.get("runtime") or {}
                self.ledger.finish_job(
                    run_id, status=runtime.get("status", "failed"),
                    qc_decision=result.get("qc_decision"),
                    evidence_validity=result.get("evidence_validity"),
                    report_ref=result.get("report_ref"),
                    error=runtime.get("last_error"))
            except Exception as error:
                logger.exception("job %s crashed", run_id)
                self.ledger.finish_job(run_id, status="failed", qc_decision=None,
                                       evidence_validity=None, report_ref=None,
                                       error=f"{type(error).__name__}: {error}")
