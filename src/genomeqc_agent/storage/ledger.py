"""Job/packet ledger on SQLite (SQLAlchemy 2.x typed mappings).

The ledger records job lifecycle and packet registrations. Tool events and
reports live in the LangGraph checkpoint DB + run directories; the ledger
only keeps structured summaries and references.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

TERMINAL = {"succeeded", "needs_human_review", "insufficient_evidence",
            "failed", "cancelled", "budget_exhausted"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class PacketRecord(Base):
    __tablename__ = "packets"

    packet_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    packet_dir: Mapped[str] = mapped_column(String(512))
    input_sha256: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), default="upload")  # upload|example
    created_at: Mapped[str] = mapped_column(String(32))


class JobRecord(Base):
    __tablename__ = "jobs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    packet_id: Mapped[str] = mapped_column(String(128))
    packet_dir: Mapped[str] = mapped_column(String(512))
    domain: Mapped[str] = mapped_column(String(64))
    user_goal: Mapped[str] = mapped_column(String(4000), default="")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    qc_decision: Mapped[str | None] = mapped_column(String(32), default=None)
    evidence_validity: Mapped[str | None] = mapped_column(String(32), default=None)
    report_ref: Mapped[str | None] = mapped_column(String(512), default=None)
    error: Mapped[str | None] = mapped_column(String(1000), default=None)
    budgets_json: Mapped[str] = mapped_column(String(500), default="{}")
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class JobStore:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(f"sqlite:///{db_path}", connect_args={
            "check_same_thread": False})
        Base.metadata.create_all(self._engine)
        self._session = sessionmaker(self._engine, expire_on_commit=False)

    # -- packets -----------------------------------------------------------
    def upsert_packet(self, packet_id: str, packet_dir: str, input_sha256: str,
                      source: str = "upload") -> None:
        with Session(self._engine) as session:
            session.merge(PacketRecord(packet_id=packet_id, packet_dir=packet_dir,
                                       input_sha256=input_sha256, source=source,
                                       created_at=_now()))
            session.commit()

    def get_packet(self, packet_id: str) -> PacketRecord | None:
        with Session(self._engine) as session:
            return session.get(PacketRecord, packet_id)

    def list_packets(self) -> list[PacketRecord]:
        with Session(self._engine) as session:
            return list(session.scalars(select(PacketRecord)))

    # -- jobs ---------------------------------------------------------------
    def create_job(self, run_id: str, packet_id: str, packet_dir: str, domain: str,
                   user_goal: str, budgets: dict) -> JobRecord:
        record = JobRecord(run_id=run_id, packet_id=packet_id, packet_dir=packet_dir,
                           domain=domain, user_goal=user_goal, status="queued",
                           budgets_json=json.dumps(budgets), created_at=_now(),
                           updated_at=_now())
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
        return record

    def _update(self, run_id: str, **fields) -> None:
        with Session(self._engine) as session:
            record = session.get(JobRecord, run_id)
            if record is None:
                return
            for key, value in fields.items():
                setattr(record, key, value)
            record.updated_at = _now()
            session.commit()

    def mark_running(self, run_id: str) -> None:
        self._update(run_id, status="running")

    def finish_job(self, run_id: str, status: str, qc_decision: str | None,
                   evidence_validity: str | None, report_ref: str | None,
                   error: str | None = None) -> None:
        self._update(run_id, status=status, qc_decision=qc_decision,
                     evidence_validity=evidence_validity, report_ref=report_ref,
                     error=error)

    def mark_cancelled(self, run_id: str) -> None:
        self._update(run_id, status="cancelled")

    def get_job(self, run_id: str) -> JobRecord | None:
        with Session(self._engine) as session:
            return session.get(JobRecord, run_id)

    def list_jobs(self) -> list[JobRecord]:
        with Session(self._engine) as session:
            return list(session.scalars(
                select(JobRecord).order_by(JobRecord.created_at.desc())))

    def close(self) -> None:
        self._engine.dispose()
