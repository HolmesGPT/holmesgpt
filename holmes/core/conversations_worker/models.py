from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ConversationStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class ConversationTask(BaseModel):
    """A claimed conversation ready for processing."""

    conversation_id: str
    account_id: str
    cluster_id: str
    origin: str
    request_sequence: int
    metadata: Dict[str, Any] = Field(default_factory=dict)
    title: Optional[str] = None

    # Raw data from the latest user_message event. Used to construct
    # ChatRequest without duplicating every field.
    user_message_data: Dict[str, Any] = Field(default_factory=dict)

    # Reconstructed from prior terminal events (ai_answer_end / approval_required).
    conversation_history: Optional[List[Dict[str, Any]]] = None


class ConversationReassignedError(Exception):
    """Raised when the conversation's assignee/request_sequence no longer matches ours."""


EVENT_USER_MESSAGE = "user_message"
