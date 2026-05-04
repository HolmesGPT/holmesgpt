from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, PrivateAttr


class ConversationStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"

    @classmethod
    def updatable_values(cls) -> tuple:
        """Statuses accepted by ``update_conversation_status``."""
        return (cls.QUEUED.value, cls.RUNNING.value, cls.COMPLETED.value, cls.FAILED.value)


class ConversationTask(BaseModel):
    """A claimed conversation ready for processing."""

    conversation_id: str
    account_id: str
    cluster_id: str
    origin: str
    request_sequence: int
    metadata: Dict[str, Any] = Field(default_factory=dict)
    title: Optional[str] = None

    # Hydrated post-construction from events; not part of the validated row schema.
    _user_message_data: Dict[str, Any] = PrivateAttr(default_factory=dict)
    _conversation_history: Optional[List[Dict[str, Any]]] = PrivateAttr(default=None)

    @property
    def user_message_data(self) -> Dict[str, Any]:
        """Raw data from the latest ``user_message`` event."""
        return self._user_message_data

    @user_message_data.setter
    def user_message_data(self, value: Dict[str, Any]) -> None:
        self._user_message_data = value

    @property
    def conversation_history(self) -> Optional[List[Dict[str, Any]]]:
        """Reconstructed from prior terminal events (ai_answer_end / approval_required)."""
        return self._conversation_history

    @conversation_history.setter
    def conversation_history(self, value: Optional[List[Dict[str, Any]]]) -> None:
        self._conversation_history = value


class ConversationReassignedError(Exception):
    """Raised when the conversation's assignee/request_sequence no longer matches ours."""


EVENT_USER_MESSAGE = "user_message"
