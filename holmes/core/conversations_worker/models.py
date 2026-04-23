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

    # Extracted from ConversationEvents after claim
    ask: Optional[str] = None
    images: Optional[List[Any]] = None
    conversation_history: Optional[List[Dict[str, Any]]] = None
    model: Optional[str] = None
    additional_system_prompt: Optional[str] = None
    tool_decisions: Optional[List[Dict[str, Any]]] = None
    frontend_tools: Optional[List[Dict[str, Any]]] = None
    frontend_tool_results: Optional[List[Dict[str, Any]]] = None
    response_format: Optional[Dict[str, Any]] = None
    behavior_controls: Optional[Dict[str, bool]] = None
    enable_tool_approval: bool = False


class ConversationReassignedError(Exception):
    """Raised when the conversation's assignee/request_sequence no longer matches ours."""


EVENT_USER_MESSAGE = "user_message"
