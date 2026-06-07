from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    content: str
    status: TaskStatus = TaskStatus.PENDING


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    INVESTIGATING = "investigating"
    SUPPORTED = "supported"
    REFUTED = "refuted"


class Hypothesis(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    # A candidate root-cause statement, e.g. "The task pod failed because the
    # cluster ran out of IP addresses".
    statement: str
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    # Short note on the evidence that supports or refutes the hypothesis.
    evidence: str = ""

