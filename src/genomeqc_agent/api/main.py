"""GenomeQC Agent API (G3, plan §7.1).

Local single-user MVP: bind 127.0.0.1 only. Public deployment requires
auth/HTTPS/data-retention work BEFORE exposing these endpoints (plan §7.1).
"""
from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile

from genomeqc_agent import __version__
from genomeqc_agent.adapters.packet_adapter import (
    PacketIntakeError,
    convert_genbank_upload,
    packet_identity,
)
from genomeqc_agent.api.schemas import (
    EventsResponse,
    Healthz,
    JobStatus,
    JobSubmitRequest,
    JobSubmitted,
    PacketInfo,
    PacketRegistered,
    ReportResponse,
    SimpleOk,
)
from genomeqc_agent.config import SUPPORTED_DOMAINS
from genomeqc_agent.jobs.worker import JobWorker
from genomeqc_agent.runtime.graph import QCGraph
from genomeqc_agent.runtime.state import TERMINAL_STATUSES
from genomeqc_agent.storage.ledger import JobStore

UPLOAD_SUFFIXES = {".gbff", ".gbk", ".gb", ".dat", ".txt"}
UPLOAD_LIMIT_BYTES = 10_000_000
EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "examples" / "public_packets"


@dataclass
class AppSettings:
    data_dir: Path = field(default_factory=lambda: Path("./data").resolve())
    examples_dir: Path = field(default_factory=lambda: EXAMPLES_DIR)
    register_examples: bool = True


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or AppSettings()
    packets_dir = settings.data_dir / "packets"
    runs_dir = settings.data_dir / "runs"
    packets_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    ledger = JobStore(settings.data_dir / "genomeqc_ledger.sqlite3")
    graph = QCGraph(db_path=settings.data_dir / "genomeqc_checkpoint.sqlite3",
                    out_dir=runs_dir)
    worker = JobWorker(graph, ledger)

    def _check_domain(domain: str) -> str:
        if domain not in SUPPORTED_DOMAINS:
            raise HTTPException(422, detail={
                "error_code": "unsupported_domain",
                "reason": f"domain must be one of {sorted(SUPPORTED_DOMAINS)}"})
        return domain

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.register_examples and settings.examples_dir.is_dir():
            for packet_dir in sorted(settings.examples_dir.iterdir()):
                if not packet_dir.is_dir():
                    continue
                if ledger.get_packet(packet_dir.name) is None:
                    meta = packet_identity(packet_dir.name, packet_dir,
                                           "auto_detect_supported")
                    ledger.upsert_packet(meta.packet_id, meta.packet_dir,
                                         meta.input_sha256, source="example")
        worker.start()
        yield
        worker.stop()
        ledger.close()
        graph.close()

    app = FastAPI(title="GenomeQC Agent", version=__version__, lifespan=lifespan)

    @app.get("/healthz", response_model=Healthz)
    def healthz() -> Healthz:
        return Healthz(version=__version__)

    @app.post("/api/v1/packets", response_model=PacketRegistered, status_code=201)
    async def upload_packet(file: UploadFile,
                            domain: str = "auto_detect_supported") -> PacketRegistered:
        _check_domain(domain)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in UPLOAD_SUFFIXES:
            raise HTTPException(422, detail={
                "error_code": "unsupported_file_type",
                "reason": f"allowed suffixes: {sorted(UPLOAD_SUFFIXES)}"})
        raw = await file.read()
        if len(raw) > UPLOAD_LIMIT_BYTES:
            raise HTTPException(413, detail={
                "error_code": "file_too_large",
                "reason": f"upload limit is {UPLOAD_LIMIT_BYTES} bytes"})
        content_sha = hashlib.sha256(raw).hexdigest()
        packet_id = f"upload_{content_sha[:12]}"
        existing = ledger.get_packet(packet_id)
        if existing is not None:
            meta = packet_identity(packet_id, Path(existing.packet_dir), domain)
            return PacketRegistered(packet_id=packet_id,
                                    input_sha256=meta.input_sha256,
                                    artifact_files=meta.artifact_files, reused=True)
        target = packets_dir / packet_id
        try:
            meta = convert_genbank_upload(raw, target, packet_id)
        except PacketIntakeError as error:
            raise HTTPException(422, detail={
                "error_code": "invalid_genbank", "reason": str(error)}) from error
        ledger.upsert_packet(packet_id, str(target), meta.input_sha256, source="upload")
        return PacketRegistered(packet_id=packet_id, input_sha256=meta.input_sha256,
                                artifact_files=meta.artifact_files, reused=False)

    @app.get("/api/v1/packets", response_model=list[PacketInfo])
    def list_packets() -> list[PacketInfo]:
        return [PacketInfo(packet_id=p.packet_id, packet_dir=p.packet_dir,
                           input_sha256=p.input_sha256, source=p.source)
                for p in ledger.list_packets()]

    @app.post("/api/v1/jobs", response_model=JobSubmitted, status_code=202)
    def submit_job(request: JobSubmitRequest) -> JobSubmitted:
        packet = ledger.get_packet(request.packet_id)
        if packet is None:
            raise HTTPException(404, detail={
                "error_code": "packet_not_found",
                "reason": f"unknown packet_id: {request.packet_id}"})
        run_id = str(uuid.uuid4())
        worker.submit(run_id, packet.packet_id, packet.packet_dir, request.domain,
                      request.user_goal,
                      {"max_tool_calls": request.max_tool_calls,
                       "max_model_requests": request.max_model_requests,
                       "max_retries_per_tool": request.max_retries_per_tool})
        return JobSubmitted(run_id=run_id, location=f"/api/v1/jobs/{run_id}")

    @app.get("/api/v1/jobs", response_model=list[JobStatus])
    def list_jobs() -> list[JobStatus]:
        return [_job_status(job) for job in ledger.list_jobs()]

    @app.get("/api/v1/jobs/{run_id}", response_model=JobStatus)
    def job_status(run_id: str) -> JobStatus:
        job = ledger.get_job(run_id)
        if job is None:
            raise HTTPException(404, detail={"error_code": "job_not_found",
                                             "reason": run_id})
        return _job_status(job)

    @app.get("/api/v1/jobs/{run_id}/events", response_model=EventsResponse)
    def job_events(run_id: str) -> EventsResponse:
        if ledger.get_job(run_id) is None:
            raise HTTPException(404, detail={"error_code": "job_not_found",
                                             "reason": run_id})
        state = graph.get_state(run_id)
        events = (state or {}).get("events", [])
        return EventsResponse(run_id=run_id, events=events)

    @app.get("/api/v1/jobs/{run_id}/report", response_model=ReportResponse)
    def job_report(run_id: str, format: str = "json") -> ReportResponse:
        job = ledger.get_job(run_id)
        if job is None:
            raise HTTPException(404, detail={"error_code": "job_not_found",
                                             "reason": run_id})
        if job.status not in TERMINAL_STATUSES or job.qc_decision is None:
            raise HTTPException(409, detail={
                "error_code": "report_not_ready",
                "reason": f"job status: {job.status}"})
        state = graph.get_state(run_id)
        report = (state or {}).get("report")
        if report is None:
            raise HTTPException(409, detail={
                "error_code": "report_not_ready",
                "reason": "checkpoint state has no report"})
        if format == "markdown":
            from genomeqc_agent.reports.composer import render_markdown
            from genomeqc_agent.schemas.report import QCReport
            return ReportResponse(run_id=run_id, format="markdown",
                                  report=render_markdown(QCReport(**report)))
        return ReportResponse(run_id=run_id, format="json", report=report)

    @app.post("/api/v1/jobs/{run_id}/resume", response_model=SimpleOk)
    def job_resume(run_id: str) -> SimpleOk:
        if ledger.get_job(run_id) is None:
            raise HTTPException(404, detail={"error_code": "job_not_found",
                                             "reason": run_id})
        state = graph.resume(run_id)
        status = (state.get("runtime") or {}).get("status", "unknown")
        return SimpleOk(run_id=run_id, outcome=f"resume_ok:{status}")

    @app.post("/api/v1/jobs/{run_id}/cancel", response_model=SimpleOk)
    def job_cancel(run_id: str) -> SimpleOk:
        outcome = worker.cancel(run_id)
        if outcome == "not_found":
            raise HTTPException(404, detail={"error_code": "job_not_found",
                                             "reason": run_id})
        return SimpleOk(run_id=run_id, outcome=outcome)

    def _job_status(job) -> JobStatus:
        return JobStatus(run_id=job.run_id, packet_id=job.packet_id,
                         domain=job.domain, status=job.status,
                         qc_decision=job.qc_decision,
                         evidence_validity=job.evidence_validity,
                         report_ref=job.report_ref, error=job.error,
                         created_at=job.created_at, updated_at=job.updated_at)

    return app
