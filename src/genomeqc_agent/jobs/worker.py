"""Single-process controlled worker (plan §2): one daemon thread consuming a
FIFO queue; SQLite ledger carries the queue state across restarts."""
from __future__ import annotations

import json
import logging
import queue
import threading

from genomeqc_agent.runtime.graph import QCGraph
from genomeqc_agent.storage.ledger import TERMINAL, JobStore

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(self, graph: QCGraph, ledger: JobStore):
        self.graph = graph
        self.ledger = ledger
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
               user_goal: str, budgets: dict) -> None:
        self.ledger.create_job(run_id, packet_id, packet_dir, domain, user_goal, budgets)
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
                budgets = self._budgets_of(run_id)
                state = self.graph.run(
                    run_id, job.packet_id, job.packet_dir, domain=job.domain,
                    user_goal=job.user_goal, **budgets)
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

    def _budgets_of(self, run_id: str) -> dict:
        job = self.ledger.get_job(run_id)
        budgets = {}
        if job is not None:
            stored = json.loads(job.budgets_json or "{}")
            for key in ("max_tool_calls", "max_model_requests", "max_retries_per_tool"):
                if key in stored:
                    budgets[key] = stored[key]
        return budgets
