"""PostgreSQL evidence and checkpoints; SQLite is supported for offline demos."""

from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON, Column, ForeignKey, Integer, MetaData, String, Table,
    create_engine, insert, select, update,
)
from sqlalchemy.engine import Engine

from tracepilot.models import DecisionState, Diagnosis, Evidence, fingerprint, utc_now


class StaleCheckpoint(RuntimeError):
    """Another worker already advanced this task."""


class EvidenceStore:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.metadata = MetaData()
        self.tasks = Table(
            "decision_agent_tasks", self.metadata,
            Column("id", String(36), primary_key=True),
            Column("revision", Integer, nullable=False),
            Column("state", JSON, nullable=False),
            Column("diagnosis", JSON),
        )
        self.evidence = Table(
            "decision_agent_evidence", self.metadata,
            Column("id", String(36), primary_key=True),
            Column("task_id", String(36), ForeignKey(self.tasks.c.id), nullable=False, index=True),
            Column("record", JSON, nullable=False),
        )
        self.events = Table(
            "decision_agent_events", self.metadata,
            Column("id", String(36), primary_key=True),
            Column("task_id", String(36), ForeignKey(self.tasks.c.id), nullable=False, index=True),
            Column("revision", Integer, nullable=False),
            Column("record", JSON, nullable=False),
        )

    @classmethod
    def from_url(cls, url: str) -> "EvidenceStore":
        return cls(create_engine(url, pool_pre_ping=True, hide_parameters=True))

    def initialize(self) -> None:
        self.metadata.create_all(self.engine)

    def create(self, state: DecisionState) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(self.tasks).values(
                id=state.task_id, revision=state.revision,
                state=state.model_dump(mode="json"),
            ))

    def checkpoint(
        self, state: DecisionState, *, event: dict[str, Any],
        evidence: Evidence | None = None, diagnosis: Diagnosis | None = None,
    ) -> None:
        """Evidence, event, and state commit together, or none of them do."""
        if evidence is not None and evidence.task_id != state.task_id:
            raise ValueError("Evidence belongs to another task")
        next_revision = state.revision + 1
        data = state.model_dump(mode="json")
        data["revision"] = next_revision
        values: dict[str, Any] = {"state": data, "revision": next_revision}
        if diagnosis is not None:
            values["diagnosis"] = diagnosis.model_dump(mode="json")
        with self.engine.begin() as conn:
            changed = conn.execute(update(self.tasks).where(
                self.tasks.c.id == state.task_id,
                self.tasks.c.revision == state.revision,
            ).values(**values))
            if changed.rowcount != 1:
                raise StaleCheckpoint("Task checkpoint changed; reload before retrying")
            if evidence is not None:
                conn.execute(insert(self.evidence).values(
                    id=evidence.id, task_id=state.task_id,
                    record=evidence.model_dump(mode="json"),
                ))
            conn.execute(insert(self.events).values(
                id=str(uuid4()), task_id=state.task_id, revision=next_revision,
                record={"at": utc_now().isoformat(), **event},
            ))
        state.revision = next_revision

    def load(self, task_id: str) -> DecisionState:
        with self.engine.connect() as conn:
            row = conn.execute(select(self.tasks.c.state).where(self.tasks.c.id == task_id)).first()
        if row is None:
            raise KeyError("Unknown task")
        return DecisionState.model_validate(row[0])

    def load_diagnosis(self, task_id: str) -> Diagnosis | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(self.tasks.c.diagnosis).where(self.tasks.c.id == task_id)).first()
        return Diagnosis.model_validate(row[0]) if row and row[0] else None

    def get_evidence(self, task_id: str, evidence_id: str) -> Evidence:
        with self.engine.connect() as conn:
            row = conn.execute(select(self.evidence.c.record).where(
                self.evidence.c.id == evidence_id, self.evidence.c.task_id == task_id,
            )).first()
        if row is None:
            raise KeyError("Evidence is not part of this task")
        return Evidence.model_validate(row[0])

    def read_evidence(self, task_id: str, evidence_id: str, offset: int = 0, limit: int = 4000) -> dict[str, Any]:
        if offset < 0 or limit < 1 or limit > 12000:
            raise ValueError("Expected offset >= 0 and 1 <= limit <= 12000")
        evidence = self.get_evidence(task_id, evidence_id)
        raw = evidence.output.model_dump_json()
        end = min(offset + limit, len(raw))
        return {
            "evidence_id": evidence_id, "offset": offset,
            "content": raw[offset:end], "total_chars": len(raw),
            "next_offset": end if end < len(raw) else None,
        }

    def audit(self, task_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return list(conn.execute(select(self.events.c.record).where(
                self.events.c.task_id == task_id,
            ).order_by(self.events.c.revision)).scalars())

    def verify(self, task_id: str) -> dict[str, Any]:
        """Detect corruption against checkpoint digests, not malicious DB administrators."""
        state = self.load(task_id)
        errors = []
        full_records = 0
        for summary in state.evidence:
            evidence_id = summary["id"]
            try:
                record = self.get_evidence(task_id, evidence_id)
                if fingerprint(record.output.model_dump()) != summary.get("sha256"):
                    errors.append({"evidence_id": evidence_id, "error": "output_digest_mismatch"})
                digest = summary.get("record_sha256")
                if digest is not None:
                    full_records += 1
                    if fingerprint(record.model_dump(mode="json")) != digest:
                        errors.append({"evidence_id": evidence_id, "error": "record_digest_mismatch"})
            except (KeyError, ValueError):
                errors.append({"evidence_id": evidence_id, "error": "missing_or_invalid_record"})
        return {"valid": not errors, "checked": len(state.evidence),
                "full_record_checks": full_records, "errors": errors}
