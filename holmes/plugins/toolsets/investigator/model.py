from enum import Enum
from pydantic import BaseModel, Field
from uuid import uuid4


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    content: str
    status: TaskStatus = TaskStatus.PENDING
