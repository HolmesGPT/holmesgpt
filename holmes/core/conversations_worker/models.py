from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, PrivateAttr


class ConversationStatus(str, Enum):
    PENDING = "pending"
    # DEPRECATED: claims now land directly in RUNNING. Kept (and still accepted
    # everywhere) for backwards compat with in-flight rows / mixed rollout.
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    # Written by the pg_cron stale sweep, and by the worker itself for
    # conversations still in flight when Holmes shuts down.
    TIMEOUT = "timeout"

    @classmethod
    def updatable_values(cls) -> tuple:
        """Statuses accepted by ``update_conversation_status`` (QUEUED kept for compat).

        TIMEOUT requires robusta-storage migration 20260817121606, which is
        applied before this ships.
        """
        return (
            cls.QUEUED.value,
            cls.RUNNING.value,
            cls.COMPLETED.value,
            cls.FAILED.value,
            cls.TIMEOUT.value,
        )


class RemoteToolCallStatus(str, Enum):
    """Status lifecycle of a RemoteToolCalls row.

    The executor (ToolCallWorker) only writes the two terminal results:
    ``COMPLETED`` (a tool_response was produced — including tool-level errors)
    and ``FAILED`` (the executor crashed before producing one). ``STOPPED``
    (relay timeout) and ``TIMEOUT`` (stale-row sweep) are written by relay /
    the claim RPC.
    """

    PENDING = "pending"
    # DEPRECATED: claims now land directly in RUNNING. Kept for compat — see
    # ConversationStatus.QUEUED.
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    TIMEOUT = "timeout"


class ConversationTask(BaseModel):
    """A claimed conversation ready for processing."""

    conversation_id: str
    account_id: str
    cluster_id: str
    origin: str
    request_sequence: int
    metadata: Dict[str, Any] = Field(default_factory=dict)
    title: Optional[str] = None
    # Conversations.user_id (RLS-bound owner). The only identity source for
    # the turn: OAuth tokens, personal skills, relay RBAC, usage attribution.
    user_id: Optional[str] = None

    @property
    def active_key(self) -> tuple:
        """In-flight key (conversation_id, request_sequence) — keyed by sequence
        too so overlapping turns of one conversation count independently for
        capacity."""
        return (self.conversation_id, self.request_sequence)

    # Hydrated post-construction from events; not part of the validated row schema.
    _user_message_data: Dict[str, Any] = PrivateAttr(default_factory=dict)
    _conversation_history: Optional[List[Dict[str, Any]]] = PrivateAttr(default=None)
    # Mid-turn follow-ups that were already in the DB when the turn was
    # claimed (sent between the claim and the first event read). Each is the
    # raw ``data`` dict of a ``user_message`` event. Fed to the model before
    # its first LLM call.
    _queued_user_messages: List[Dict[str, Any]] = PrivateAttr(default_factory=list)
    # Seq of the newest ConversationEvents row whose user_message the model
    # has seen. None when the RPC does not report seq (older database).
    _consumed_seq: Optional[int] = PrivateAttr(default=None)

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

    @property
    def queued_user_messages(self) -> List[Dict[str, Any]]:
        return self._queued_user_messages

    @queued_user_messages.setter
    def queued_user_messages(self, value: List[Dict[str, Any]]) -> None:
        self._queued_user_messages = value

    @property
    def consumed_seq(self) -> Optional[int]:
        return self._consumed_seq

    @consumed_seq.setter
    def consumed_seq(self, value: Optional[int]) -> None:
        self._consumed_seq = value


class ConversationReassignedError(Exception):
    """Raised when the conversation's assignee/request_sequence no longer matches ours."""


class PendingFollowupError(Exception):
    """Raised when the DB refuses to complete a turn because a user message
    newer than the one the model last saw is waiting (ROB-1499)."""


EVENT_USER_MESSAGE = "user_message"
