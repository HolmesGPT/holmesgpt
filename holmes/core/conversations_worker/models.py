import logging
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

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
# executor that must run it; callers may introduce further names without a
# Holmes release (see sizing.py for how an executor is sized).
MANUAL_EXECUTOR = "manual"  # a person is waiting: chat, follow-ups, "Investigate now"
AUTO_EXECUTOR = "auto"  # background: auto-triage, bulk investigations, workflows
# What a row gets when nothing names an executor (the DB column default too).
DEFAULT_EXECUTOR = MANUAL_EXECUTOR

# Executor names come from broadcast payloads and DB rows written by other
# services; keep them to a conservative slug so a bad payload can't name an
# executor something unloggable or unbounded. Same rule as the DB CHECK and relay.
_EXECUTOR_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def is_valid_executor_name(name: object) -> bool:
    return isinstance(name, str) and bool(_EXECUTOR_NAME_RE.fullmatch(name))


# Error events the worker writes for a row it will never finish. The codes are
# a contract with the frontend (unmapped codes render ``description`` as-is).
#
# Shutdown: when the pod is asked to stop (SIGTERM from a rollout, node drain,
# scale-down), conversations mid-turn are never going to finish — the threads
# are daemons and nothing else picks the row back up (the claim RPCs only take
# 'pending'). They are retired with this error event, then status 'timeout'.
SHUTDOWN_REASON = "Holmes Restarted"
SHUTDOWN_ERROR_DESCRIPTION = (
    f"{SHUTDOWN_REASON} — this request was interrupted before it finished. "
    "Ask again to retry."
)
SHUTDOWN_ERROR_CODE = 5205
# A row naming an executor this agent cannot run (executor cap reached).
EXECUTOR_UNAVAILABLE_ERROR_CODE = 5206


def conversation_identity(conv: Dict[str, Any]) -> Optional[Tuple[str, int]]:
    """``(conversation_id, request_sequence)`` of a Conversations row, or None
    when the row does not carry a usable identity."""
    cid = conv.get("conversation_id")
    seq = conv.get("request_sequence", 1)
    if not cid:
        return None
    try:
        return str(cid), int(seq)
    except (TypeError, ValueError):
        return None


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
    def from_row(cls, conv: Dict[str, Any]) -> Optional["ConversationTask"]:
        """A task from a claimed Conversations row, or None for a row that does
        not parse (logged)."""
        identity = conversation_identity(conv)
        if identity is None:
            logging.error(
                "Conversation row without a usable identity: %s",
                {k: conv.get(k) for k in ("conversation_id", "request_sequence")},
            )
            return None
        try:
            return cls(
                conversation_id=identity[0],
                account_id=conv["account_id"],
                cluster_id=conv["cluster_id"],
                origin=conv.get("origin", "chat"),
                request_sequence=identity[1],
                metadata=conv.get("metadata") or {},
                title=conv.get("title"),
                user_id=conv.get("user_id"),
                executor=conv.get("executor") or DEFAULT_EXECUTOR,
            )
        except Exception:
            logging.exception(
                "Failed to build conversation task from row (conversation_id=%s)",
                identity[0],
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
