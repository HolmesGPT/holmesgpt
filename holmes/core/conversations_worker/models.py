import logging
import re
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


# Executor names (ROB-1369). A Conversations row's ``executor`` column names the
# pool that must run it; callers may introduce further names without a Holmes
# release (see sizing.py for how a pool is sized).
DEFAULT_EXECUTOR = "manual"
AUTO_EXECUTOR = "auto"

# Executor names come from broadcast payloads and DB rows written by other
# services; keep them to a conservative slug so a bad payload can't name a
# pool something unloggable or unbounded. Same rule as the DB CHECK and relay.
_EXECUTOR_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def is_valid_executor_name(name: object) -> bool:
    return isinstance(name, str) and bool(_EXECUTOR_NAME_RE.fullmatch(name))


class ConversationTask(BaseModel):
    """A claimed conversation ready for processing."""

    conversation_id: str
    account_id: str
    cluster_id: str
    origin: str
    request_sequence: int
    executor: str = DEFAULT_EXECUTOR
    metadata: Dict[str, Any] = Field(default_factory=dict)
    title: Optional[str] = None
    # Conversations.user_id (RLS-bound owner). The only identity source for
    # the turn: OAuth tokens, personal skills, relay RBAC, usage attribution.
    user_id: Optional[str] = None

    @classmethod
    def from_row(
        cls, conv: Dict[str, Any], executor: Optional[str] = None
    ) -> Optional["ConversationTask"]:
        """A task from a claimed Conversations row, or None for a row that does
        not parse (logged). ``executor`` is the pool that claimed the row and
        wins over the column: a filtered claim only returns rows naming it."""
        try:
            return cls(
                conversation_id=conv["conversation_id"],
                account_id=conv["account_id"],
                cluster_id=conv["cluster_id"],
                origin=conv.get("origin", "chat"),
                request_sequence=int(conv.get("request_sequence", 1)),
                metadata=conv.get("metadata") or {},
                title=conv.get("title"),
                user_id=conv.get("user_id"),
                executor=executor or conv.get("executor") or DEFAULT_EXECUTOR,
            )
        except Exception:
            logging.exception(
                "Failed to build conversation task from row (conversation_id=%s)",
                conv.get("conversation_id", "unknown"),
                exc_info=True,
            )
            return None

    @property
    def active_key(self) -> tuple:
        """In-flight key (conversation_id, request_sequence) — keyed by sequence
        too so overlapping turns of one conversation count independently for
        capacity."""
        return (self.conversation_id, self.request_sequence)

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
