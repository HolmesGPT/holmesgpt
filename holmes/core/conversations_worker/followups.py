"""Mid-turn follow-ups (ROB-1499).

A user message posted while a turn is running lands in ConversationEvents as a
``user_message`` with ``data.mid_turn = true``, and the Conversations row is
left untouched. Two things bring it to the model:

* the ``conversation_followup`` broadcast wakes the running worker, which
  reads the new rows at its next step boundary and appends them as user turns;
* ``update_conversation_status`` refuses to complete the turn while a user
  message newer than ``consumed_seq`` exists, so a lost broadcast only delays
  the message to the end of the turn instead of dropping it.

``MidTurnFollowups`` owns that bookkeeping for one running turn.
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from holmes.core.conversations_worker.models import EVENT_USER_MESSAGE

if TYPE_CHECKING:
    from holmes.core.supabase_dal import SupabaseDal

# Prefixed to every mid-turn message so the model knows it is not a reply to
# the answer it was about to give. Persisted into the turn's history.
MID_TURN_NOTE = (
    "The user sent the following message while you were still working on "
    "their previous request. Take it into account and continue: it may add "
    "information, change direction, or ask something new."
)


def format_mid_turn_user_message(ask: str) -> Dict[str, Any]:
    return {"role": "user", "content": f"{MID_TURN_NOTE}\n\n{ask}"}


def is_mid_turn_user_message(event: Dict[str, Any]) -> bool:
    data = event.get("data") or {}
    return (
        event.get("event") == EVENT_USER_MESSAGE
        and bool(data.get("mid_turn"))
        and bool(data.get("ask"))
    )


class MidTurnFollowups:
    """Bookkeeping for the follow-ups of one running turn.

    ``pending_user_messages`` is the hook ``call_stream`` polls at step
    boundaries. ``fetch_new`` is what the worker calls when the DB refused to
    complete the turn. Both advance ``consumed_seq``.
    """

    def __init__(
        self,
        dal: "SupabaseDal",
        conversation_id: str,
        signal: threading.Event,
        consumed_seq: Optional[int],
        queued: Optional[List[Dict[str, Any]]] = None,
        enabled: bool = True,
        poll_interval_seconds: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.dal = dal
        self.conversation_id = conversation_id
        self.signal = signal
        self.consumed_seq = consumed_seq
        # Raw user_message data dicts that were already in the DB when the
        # turn was claimed. Delivered on the first poll.
        self._queued: List[Dict[str, Any]] = list(queued or [])
        # False when the database cannot report seq (no ROB-1499 migration):
        # nothing can be tracked, so nothing is fetched.
        self.enabled = enabled and consumed_seq is not None
        self.delivered_count = 0
        # The broadcast is best-effort (a realtime socket can be down for a
        # whole turn), so step boundaries also read the DB every
        # poll_interval_seconds. None or 0 disables the poll.
        self._poll_interval = poll_interval_seconds or 0
        self._clock = clock
        self._last_fetch = clock()

    # ---- hooks ----

    def pending_user_messages(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if self._queued:
            for data in self._queued:
                out.append(format_mid_turn_user_message(str(data.get("ask"))))
            self._queued = []
        if self.enabled and (self.signal.is_set() or self._poll_due()):
            self.signal.clear()
            out.extend(self.fetch_new())
        if out:
            self.delivered_count += len(out)
            logging.info(
                "Conversation %s: delivering %d mid-turn user message(s) (consumed_seq=%s)",
                self.conversation_id,
                len(out),
                self.consumed_seq,
            )
        return out

    def _poll_due(self) -> bool:
        return (
            self._poll_interval > 0
            and self._clock() - self._last_fetch >= self._poll_interval
        )

    def fetch_new(self) -> List[Dict[str, Any]]:
        """Read user messages newer than ``consumed_seq`` and consume them.

        Compacted rows are included on purpose: the ai_answer_end flush marks
        every earlier row compacted, a follow-up that landed just before it
        included.
        """
        if not self.enabled or self.consumed_seq is None:
            return []
        self._last_fetch = self._clock()
        try:
            events = self.dal.get_conversation_events(
                self.conversation_id,
                include_compacted=True,
                min_seq=self.consumed_seq + 1,
            )
        except Exception:
            logging.exception(
                "Conversation %s: failed to read mid-turn follow-ups",
                self.conversation_id,
            )
            return []
        out: List[Dict[str, Any]] = []
        for ev in events:
            seq = ev.get("seq")
            if not isinstance(seq, int) or seq <= self.consumed_seq:
                continue
            if not is_mid_turn_user_message(ev):
                continue
            out.append(format_mid_turn_user_message(str(ev["data"]["ask"])))
            self.consumed_seq = max(self.consumed_seq, seq)
        return out


class TerminalMessagesCapture:
    """Pass-through over a stream that remembers the ``messages`` array of the
    last terminal event, so a refused completion can continue from the exact
    history the model ended on."""

    def __init__(self, stream: Any, terminal_events: Any):
        self._stream = stream
        self._terminal_events = terminal_events
        self.messages: Optional[List[Dict[str, Any]]] = None

    def __iter__(self):
        for message in self._stream:
            if message.event in self._terminal_events:
                msgs = (message.data or {}).get("messages")
                if msgs:
                    self.messages = msgs
            yield message


FollowupProvider = Callable[[], List[Dict[str, Any]]]
