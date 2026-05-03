import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional, TYPE_CHECKING

from holmes.core.conversations_worker.models import ConversationReassignedError
from holmes.utils.stream import StreamEvents, StreamMessage

if TYPE_CHECKING:
    from holmes.core.supabase_dal import SupabaseDal


# Events that end a turn and must be flushed immediately.  The publisher
# reports the last one it saw back to the worker so the conversation status
# can be set appropriately.
_TERMINAL_EVENTS = {
    StreamEvents.ANSWER_END,
    StreamEvents.APPROVAL_REQUIRED,
    StreamEvents.ERROR,
}

# Events that should cause an immediate flush.  Terminal events end a turn;
# CONVERSATION_HISTORY_COMPACTED isn't terminal but carries the same
# "history snapshot + prior events superseded" semantics so it's flushed +
# compacted with the same logic.  TOKEN_COUNT events are flushed eagerly:
# call_stream() emits one right after the LLM response (before tool execution
# starts) and one right after the last TOOL_RESULT of a batch (before the
# next LLM call).  Both boundaries precede a long-running step (>1s tool
# work or LLM call), so flushing here keeps subscribers up to date without
# the per-tool write amplification of flushing on every TOOL_RESULT.
_FLUSH_IMMEDIATELY_EVENTS = _TERMINAL_EVENTS | {
    StreamEvents.CONVERSATION_HISTORY_COMPACTED,
    StreamEvents.TOKEN_COUNT,
}

# Events whose `messages` array carries the full conversation history
# snapshot — all prior events are superseded and should be marked compacted.
_COMPACT_ON_FLUSH_EVENTS = {
    StreamEvents.ANSWER_END,
    StreamEvents.APPROVAL_REQUIRED,
    StreamEvents.CONVERSATION_HISTORY_COMPACTED,
}


class ConversationEventPublisher:
    """
    Consumes StreamMessage events from call_stream() and batches them
    into ConversationEvents rows in Supabase.
    """

    def __init__(
        self,
        dal: "SupabaseDal",
        conversation_id: str,
        assignee: str,
        request_sequence: int,
        batch_interval_seconds: float = 1.0,
    ):
        self.dal = dal
        self.conversation_id = conversation_id
        self.assignee = assignee
        self.request_sequence = request_sequence
        self.batch_interval_seconds = batch_interval_seconds

        self._pending_events: List[Dict[str, Any]] = []
        self._last_flush_time: float = time.monotonic()
        self._last_retry_time: float = 0.0
        self._lock = threading.Lock()

        self._last_terminal_event: Optional[StreamEvents] = None

        # Sticky compact flag: set when a compact flush is attempted but the
        # DAL returns None. Ensures the compact intent is preserved across
        # retries and the final drain.
        self._pending_compact: bool = False

    def consume(
        self,
        stream: Generator[StreamMessage, None, None],
    ) -> Optional[StreamEvents]:
        """
        Drain the stream generator, batching events and writing them to the DB.
        Returns the terminal StreamEvents value observed, or None if the stream ended
        without a terminal event.
        Raises ConversationReassignedError if the conversation was reassigned mid-stream.
        """
        reassigned = False
        try:
            for message in stream:
                self._append_event(message)
                if message.event in _TERMINAL_EVENTS:
                    self._last_terminal_event = message.event
                # Flush on terminal events immediately, or when interval elapses
                if message.event in _FLUSH_IMMEDIATELY_EVENTS:
                    # ai_answer_end / approval_required / compacted carry a
                    # full conversation history snapshot in their messages
                    # array, so all prior events are superseded → compact.
                    if message.event in _COMPACT_ON_FLUSH_EVENTS:
                        self._pending_compact = True
                    self._flush()
                elif (
                    time.monotonic() - self._last_flush_time
                    >= self.batch_interval_seconds
                    and time.monotonic() - self._last_retry_time
                    >= self.batch_interval_seconds
                ):
                    self._flush()
        except ConversationReassignedError:
            reassigned = True
            raise
        finally:
            # Final drain of any remaining events — skip if the conversation
            # was reassigned, since our assignee/sequence are stale and writing
            # would either fail or race with the new owner.
            if not reassigned:
                self._flush()

        # If events remain unsaved after the stream is fully consumed, the
        # terminal batch was lost (repeated None returns). Surface this to the
        # caller so the conversation is marked failed rather than completed.
        with self._lock:
            remaining = len(self._pending_events)
        if remaining > 0:
            logging.error(
                "consume() finished with %d unsaved events for conversation %s",
                remaining,
                self.conversation_id,
            )
            return None

        return self._last_terminal_event

    def _append_event(self, message: StreamMessage) -> None:
        with self._lock:
            self._pending_events.append(
                {
                    "event": message.event.value,
                    "data": message.data,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )

    def _flush(self) -> None:
        with self._lock:
            if not self._pending_events:
                return
            # Snapshot but don't clear yet — only clear after a successful post.
            events_to_flush = list(self._pending_events)
            compact = self._pending_compact

        try:
            seq = self.dal.post_conversation_events(
                conversation_id=self.conversation_id,
                assignee=self.assignee,
                request_sequence=self.request_sequence,
                events=events_to_flush,
                compact=compact,
            )
        except ConversationReassignedError:
            raise
        except Exception as e:
            # The RPCs prefix mismatch errors (status / assignee / request_sequence)
            # with "MISMATCH " — promote those to ConversationReassignedError so
            # the worker can exit the processing loop cleanly.
            if "mismatch" in str(e).lower():
                raise ConversationReassignedError(str(e)) from e
            raise

        if seq is None:
            # DAL returned None (disabled or unexpected empty response).
            # Keep events and compact flag in memory so the next flush retries.
            # Update _last_retry_time to throttle retries independently of
            # normal flush timing.
            self._last_retry_time = time.monotonic()
            logging.warning(
                "post_conversation_events returned None for conversation %s — "
                "events retained for retry (%d events, compact=%s)",
                self.conversation_id,
                len(events_to_flush),
                compact,
            )
            return

        # Success — remove the flushed events and clear the compact flag.
        # New events may have been appended while the RPC was in flight,
        # so we remove only the count we just posted.
        with self._lock:
            del self._pending_events[: len(events_to_flush)]
            self._pending_compact = False
        self._last_flush_time = time.monotonic()
        logging.debug(
            "Posted %d events to conversation %s (seq=%s, compact=%s)",
            len(events_to_flush),
            self.conversation_id,
            seq,
            compact,
        )
