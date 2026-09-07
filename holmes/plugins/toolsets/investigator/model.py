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
        # String task: "Check pod status" -> {"content": "Check pod status"}
        if isinstance(data, str):
            return {"content": data}

        # Dict task: sanitize invalid status enum values to default
        if isinstance(data, dict):
            status = data.get("status")
            if status and status not in TaskStatus._value2member_map_:
                data = {**data, "status": TaskStatus.PENDING.value}
            return data

        return data

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "content": self.content,
            "status": self.status.value,
        }

