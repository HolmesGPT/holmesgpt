from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    content: str
    status: TaskStatus = TaskStatus.PENDING

    @model_validator(mode="before")
    @classmethod
    def _coerce_input(cls, data: Any) -> Any:
        # Some LLMs return tasks as raw strings instead of structured objects;
        # coerce to dict format to preserve task content in the expected model structure.
        if isinstance(data, str):
            return {"content": data}

        # LLMs may produce unsupported or malformed status strings;
        # fallback to pending to avoid validation failures while preserving the task.
        if isinstance(data, dict):
            status = data.get("status")
            try:
                TaskStatus(status)
            except (ValueError, TypeError):
                data = {**data, "status": TaskStatus.PENDING.value}
            return data

        return data

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "content": self.content,
            "status": self.status.value,
        }
