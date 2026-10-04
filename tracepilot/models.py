"""Validated boundaries between the LLM, decision model, tools, and storage."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tracepilot.errors import ToolErrorKind


TaskStatus = Literal["running", "completed", "needs_input", "stopped", "failed"]
TerminalStatus = Literal["completed", "needs_input", "stopped", "failed"]
ToolStatus = Literal["success", "no_data", "error", "approval_required"]
DiagnosisOutcome = Literal["legacy_unverified", "candidate_causes", "insufficient_evidence", "invalid_report"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ToolSpec(Record):
    name: str
    description: str
    parameters: dict[str, Any]
    source: str
    # Set by operator configuration, never by an LLM or remote tool description.
    retry_safe: bool = False
    output_schema: dict[str, Any] | None = None
    approval_required: bool = False

    @property
    def version(self) -> str:
        return fingerprint(self.model_dump())


class Candidate(Record):
    id: str = Field(min_length=1, max_length=80)
    tool: str
    arguments: dict[str, Any]
    purpose: str = Field(min_length=1, max_length=2000)
    direction: str = Field(min_length=1, max_length=200)
    # Filled by the engine, never trusted from model output.
    tool_version: str = ""

    @property
    def fingerprint(self) -> str:
        return fingerprint({"tool": self.tool, "version": self.tool_version, "arguments": self.arguments})


class Claim(Record):
    text: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)


class TaskMemory(Record):
    facts: list[Claim] = Field(default_factory=list, max_length=30)
    hypotheses: list[Claim] = Field(default_factory=list, max_length=20)
    counterevidence: list[Claim] = Field(default_factory=list, max_length=20)
    verification_results: list[Claim] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=20)

    def validate_references(self, known_ids: set[str]) -> None:
        for field in ("facts", "hypotheses", "counterevidence", "verification_results"):
            for claim in getattr(self, field):
                if not set(claim.evidence_ids) <= known_ids:
                    raise ValueError("Memory references unknown evidence")
                if field != "hypotheses" and not claim.evidence_ids:
                    raise ValueError(f"{field} must cite evidence; use hypotheses for guesses")


class ReasoningResult(Record):
    memory: TaskMemory
    candidates: list[Candidate] = Field(default_factory=list, max_length=20)


class Decision(Record):
    choice: str
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[str, float] = Field(default_factory=dict)


class ToolOutput(Record):
    status: ToolStatus
    data: Any = None
    error: str | None = None
    error_kind: ToolErrorKind | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0, le=3600)


class Evidence(Record):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    tool: str
    source: str
    arguments: dict[str, Any]
    collected_at: datetime = Field(default_factory=utc_now)
    elapsed_seconds: float = 0
    output: ToolOutput
    attempt_id: str = ""
    attempt_no: int = 0

    def summary(self, max_chars: int = 3000) -> dict[str, Any]:
        raw = self.output.model_dump_json()
        return {
            "id": self.id,
            "tool": self.tool,
            "source": self.source,
            "arguments": self.arguments,
            "collected_at": self.collected_at.isoformat(),
            "status": self.output.status,
            "attempt_id": self.attempt_id,
            "attempt_no": self.attempt_no,
            "error_kind": self.output.error_kind,
            "preview": raw[:max_chars],
            "truncated": len(raw) > max_chars,
            "total_chars": len(raw),
            "sha256": fingerprint(self.output.model_dump()),
            "record_sha256": fingerprint(self.model_dump(mode="json")),
        }


class ActionRecord(Record):
    choice: str
    confidence: float | None = None
    direction: str = ""
    fingerprint: str = ""
    evidence_id: str | None = None
    status: str
    attempt_id: str = ""
    attempt_no: int = 0
    error_kind: ToolErrorKind | None = None
    retry_exhausted: bool = False
    feedback: str = Field(default="", max_length=2000)


class Budget(Record):
    max_steps: int = Field(default=20, ge=1, le=100)
    max_tool_calls: int = Field(default=10, ge=1, le=100)
    max_reasoning_calls: int = Field(default=5, ge=1, le=30)
    max_seconds: float = Field(default=300, gt=0, le=3600)
    min_confidence: float = Field(default=0.65, ge=0, le=1)
    max_no_progress: int = Field(default=3, ge=1, le=20)
    max_tool_attempts: int = Field(default=3, ge=1, le=5)
    max_context_chars: int = Field(default=160000, ge=8000, le=1000000)


class InvestigationScope(Record):
    service: str = Field(min_length=1, max_length=200)
    namespace: str = Field(min_length=1, max_length=200)
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def bounded_window(self) -> "InvestigationScope":
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("Scope timestamps must include a timezone")
        if not 0 < (self.end - self.start).total_seconds() <= 86400:
            raise ValueError("Scope must span more than zero and at most 24 hours")
        return self


class DecisionState(Record):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    question: str = Field(min_length=1, max_length=16000)
    context: dict[str, Any] = Field(default_factory=dict)
    scope: InvestigationScope | None = None
    memory: TaskMemory = Field(default_factory=TaskMemory)
    candidates: list[Candidate] = Field(default_factory=list)
    available_tools: list[ToolSpec] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    history: list[ActionRecord] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    steps: int = 0
    tool_calls: int = 0
    reasoning_calls: int = 0
    no_progress: int = 0
    direction: str = ""
    status: TaskStatus = "running"
    # A terminal request is committed with the validated report, not before it.
    pending_status: TerminalStatus | None = None
    stop_reason: str = ""
    revision: int = 0
    created_at: datetime = Field(default_factory=utc_now)

    def model_state(self) -> dict[str, Any]:
        """Bound context independently of the durable full evidence records."""
        result = self.model_dump(mode="json", exclude={"revision"})
        result["evidence"] = self.evidence[-12:]
        result["history"] = [h.model_dump() for h in self.history[-20:]]
        result["known_evidence_ids"] = [e["id"] for e in self.evidence]
        return result


class DiagnosisDraft(Record):
    """LLM output has no free-text summary or model-selected outcome."""

    memory: TaskMemory
    causes: list[Claim] = Field(default_factory=list, max_length=20)
    verification_steps: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=20)

    def validate_references(self, known_ids: set[str]) -> None:
        self.memory.validate_references(known_ids)
        for cause in self.causes:
            if not cause.evidence_ids or not set(cause.evidence_ids) <= known_ids:
                raise ValueError("Every reported cause must cite known evidence")


class Diagnosis(Record):
    # Preserve the stored v1 shape; new output is rendered from DiagnosisDraft.
    summary: str = Field(min_length=1, max_length=12000)
    causes: list[Claim] = Field(default_factory=list, max_length=20)
    verification_steps: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    schema_version: int = 1
    outcome: DiagnosisOutcome = "legacy_unverified"


class InvestigationResult(Record):
    state: DecisionState
    diagnosis: Diagnosis
