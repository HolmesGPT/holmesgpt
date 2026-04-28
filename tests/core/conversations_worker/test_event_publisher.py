"""Unit tests for the ConversationEventPublisher."""
from typing import Any, List, Optional

import pytest

from holmes.core.conversations_worker.event_publisher import (
    ConversationEventPublisher,
)
from holmes.core.conversations_worker.models import ConversationReassignedError
from holmes.utils.stream import StreamEvents, StreamMessage


class _FakeDal:
    """Minimal fake of SupabaseDal for unit tests."""

    def __init__(self, seq_start: int = 0, raise_mismatch: bool = False):
        self.calls: List[dict] = []
        self._next_seq = seq_start
        self._raise_mismatch = raise_mismatch

    def post_conversation_events(
        self,
        conversation_id: str,
        assignee: str,
        request_sequence: int,
        events: list,
        compact: bool = False,
    ) -> Optional[int]:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "assignee": assignee,
                "request_sequence": request_sequence,
                "events": events,
                "compact": compact,
            }
        )
        if self._raise_mismatch:
            raise Exception("Assignee mismatch: expected X, got Y")
        self._next_seq += 1
        return self._next_seq


def _stream(events: List[StreamMessage]):
    for e in events:
        yield e


def test_publisher_flushes_on_terminal_answer_end():
    dal = _FakeDal()
    pub = ConversationEventPublisher(
        dal=dal,
        conversation_id="c1",
        assignee="h1",
        request_sequence=1,
        batch_interval_seconds=60.0,  # long — no interval flush
    )
    terminal = pub.consume(
        _stream(
            [
                StreamMessage(event=StreamEvents.AI_MESSAGE, data={"content": "thinking"}),
                StreamMessage(event=StreamEvents.ANSWER_END, data={"content": "done"}),
            ]
        )
    )
    assert terminal == StreamEvents.ANSWER_END
    # Both events should be in a single batch (flushed on ANSWER_END)
    assert len(dal.calls) == 1
    assert len(dal.calls[0]["events"]) == 2
    # ai_answer_end carries the full conversation history snapshot —
    # all prior events are superseded and should be compacted.
    assert dal.calls[0]["compact"] is True
    assert dal.calls[0]["events"][0]["event"] == "ai_message"
    assert dal.calls[0]["events"][1]["event"] == "ai_answer_end"


def test_publisher_flushes_on_approval_required():
    dal = _FakeDal()
    pub = ConversationEventPublisher(
        dal=dal,
        conversation_id="c1",
        assignee="h1",
        request_sequence=1,
        batch_interval_seconds=60.0,
    )
    terminal = pub.consume(
        _stream(
            [
                StreamMessage(event=StreamEvents.START_TOOL, data={"tool_name": "bash"}),
                StreamMessage(
                    event=StreamEvents.APPROVAL_REQUIRED,
                    data={"pending_approvals": [{"tool_call_id": "1"}]},
                ),
            ]
        )
    )
    assert terminal == StreamEvents.APPROVAL_REQUIRED
    assert len(dal.calls) == 1
    # approval_required also carries the full conversation history snapshot.
    assert dal.calls[0]["compact"] is True


def test_publisher_compact_flag_on_compacted_event():
    """Only the CONVERSATION_HISTORY_COMPACTED event triggers compact=True."""
    dal = _FakeDal()
    pub = ConversationEventPublisher(
        dal=dal,
        conversation_id="c1",
        assignee="h1",
        request_sequence=1,
        batch_interval_seconds=60.0,
    )
    pub.consume(
        _stream(
            [
                StreamMessage(
                    event=StreamEvents.CONVERSATION_HISTORY_COMPACTION_START,
                    data={"content": "compacting"},
                ),
                StreamMessage(
                    event=StreamEvents.CONVERSATION_HISTORY_COMPACTED,
                    data={"content": "done"},
                ),
                StreamMessage(event=StreamEvents.ANSWER_END, data={"content": "final"}),
            ]
        )
    )
    # Two flushes with compact=True: the compacted event AND the ai_answer_end.
    compact_calls = [c for c in dal.calls if c["compact"] is True]
    assert len(compact_calls) == 2, f"expected 2 compact=True calls, got {len(compact_calls)}"
    # First compact=True call must carry the compacted event.
    compact_event_types = [e["event"] for e in compact_calls[0]["events"]]
    assert "conversation_history_compacted" in compact_event_types

    # Second compact=True call must carry ai_answer_end.
    answer_event_types = [e["event"] for e in compact_calls[1]["events"]]
    assert "ai_answer_end" in answer_event_types

    # Every event appears exactly once across all calls (no double-posting).
    all_event_types = [e["event"] for c in dal.calls for e in c["events"]]
    assert all_event_types.count("conversation_history_compacted") == 1
    assert all_event_types.count("conversation_history_compaction_start") == 1
    assert all_event_types.count("ai_answer_end") == 1


def test_publisher_compaction_start_does_not_trigger_compact_flag():
    """Only CONVERSATION_HISTORY_COMPACTED triggers compact=True, never START."""
    dal = _FakeDal()
    pub = ConversationEventPublisher(
        dal=dal,
        conversation_id="c1",
        assignee="h1",
        request_sequence=1,
        batch_interval_seconds=60.0,
    )
    pub.consume(
        _stream(
            [
                StreamMessage(
                    event=StreamEvents.CONVERSATION_HISTORY_COMPACTION_START,
                    data={"content": "compacting"},
                ),
                # Compaction aborted/unfinished — no COMPACTED event ever fires
                StreamMessage(event=StreamEvents.ANSWER_END, data={"content": "done"}),
            ]
        )
    )
    # The only compact=True should be from the ANSWER_END (which always compacts).
    # The COMPACTION_START alone must NOT trigger compact.
    compact_calls = [c for c in dal.calls if c["compact"] is True]
    assert len(compact_calls) == 1
    # That one compact call must be the ANSWER_END batch
    assert any(e["event"] == "ai_answer_end" for e in compact_calls[0]["events"])


def test_publisher_only_terminal_compacts_without_compaction_events():
    """Normal flows (no COMPACTED event) should still compact on ANSWER_END."""
    dal = _FakeDal()
    pub = ConversationEventPublisher(
        dal=dal,
        conversation_id="c1",
        assignee="h1",
        request_sequence=1,
        batch_interval_seconds=60.0,
    )
    pub.consume(
        _stream(
            [
                StreamMessage(event=StreamEvents.START_TOOL, data={"tool_name": "t"}),
                StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_call_id": "t"}),
                StreamMessage(event=StreamEvents.AI_MESSAGE, data={"content": "thinking"}),
                StreamMessage(event=StreamEvents.TOKEN_COUNT, data={}),
                StreamMessage(event=StreamEvents.ANSWER_END, data={"content": "final"}),
            ]
        )
    )
    # Only the ANSWER_END flush should have compact=True
    compact_calls = [c for c in dal.calls if c["compact"] is True]
    assert len(compact_calls) == 1
    assert any(e["event"] == "ai_answer_end" for e in compact_calls[0]["events"])


def test_publisher_raises_on_reassignment():
    dal = _FakeDal(raise_mismatch=True)
    pub = ConversationEventPublisher(
        dal=dal,
        conversation_id="c1",
        assignee="h1",
        request_sequence=1,
    )
    with pytest.raises(ConversationReassignedError):
        pub.consume(
            _stream(
                [
                    StreamMessage(event=StreamEvents.ANSWER_END, data={"content": "x"}),
                ]
            )
        )


def test_publisher_flushes_on_error_event():
    """ERROR events are terminal and must be flushed immediately."""
    dal = _FakeDal()
    pub = ConversationEventPublisher(
        dal=dal,
        conversation_id="c1",
        assignee="h1",
        request_sequence=1,
        batch_interval_seconds=60.0,
    )
    terminal = pub.consume(
        _stream(
            [
                StreamMessage(event=StreamEvents.AI_MESSAGE, data={"content": "hi"}),
                StreamMessage(
                    event=StreamEvents.ERROR,
                    data={"description": "rate limit", "error_code": 5204},
                ),
            ]
        )
    )
    assert terminal == StreamEvents.ERROR
    # Both events in a single batch (flushed on ERROR)
    assert len(dal.calls) == 1
    assert dal.calls[0]["events"][-1]["event"] == "error"
    # ERROR events do NOT carry a conversation history snapshot — no compaction.
    assert dal.calls[0]["compact"] is False


def test_publisher_covers_all_stream_event_types():
    """Sanity check that every StreamEvents value is accepted by the publisher."""
    dal = _FakeDal()
    pub = ConversationEventPublisher(
        dal=dal,
        conversation_id="c1",
        assignee="h1",
        request_sequence=1,
        batch_interval_seconds=60.0,
    )
    # Build a message for each StreamEvents value
    all_events = [StreamMessage(event=e, data={}) for e in StreamEvents]
    # Consume — publisher should never crash
    pub.consume(_stream(all_events))
    # Collect all event type strings actually written
    written = {ev["event"] for call in dal.calls for ev in call["events"]}
    expected = {e.value for e in StreamEvents}
    assert expected == written, f"missing from writes: {expected - written}"


def test_publisher_batches_intermediate_events():
    """Events that don't trigger immediate flush should be batched together."""
    dal = _FakeDal()
    pub = ConversationEventPublisher(
        dal=dal,
        conversation_id="c1",
        assignee="h1",
        request_sequence=1,
        batch_interval_seconds=60.0,  # very large — no interval flush
    )
    pub.consume(
        _stream(
            [
                StreamMessage(event=StreamEvents.START_TOOL, data={"tool_name": "t1"}),
                StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_call_id": "1"}),
                StreamMessage(event=StreamEvents.TOKEN_COUNT, data={}),
                StreamMessage(event=StreamEvents.ANSWER_END, data={"content": "ok"}),
            ]
        )
    )
    # All 4 should end up in a single flush (final ANSWER_END)
    assert len(dal.calls) == 1
    assert len(dal.calls[0]["events"]) == 4


def test_publisher_sticky_compact_across_none_retry():
    """When a compact flush returns None, the compact flag must persist
    so the retry uses compact=True."""
    call_count = {"n": 0}

    class _RetryDal(_FakeDal):
        def post_conversation_events(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First attempt returns None (retry)
                self.calls.append(kwargs)
                return None
            return super().post_conversation_events(**kwargs)

    dal = _RetryDal()
    pub = ConversationEventPublisher(
        dal=dal,
        conversation_id="c1",
        assignee="h1",
        request_sequence=1,
        batch_interval_seconds=0.0,  # flush on every message
    )
    terminal = pub.consume(
        _stream(
            [
                StreamMessage(event=StreamEvents.AI_MESSAGE, data={"content": "hi"}),
                StreamMessage(event=StreamEvents.ANSWER_END, data={"content": "done"}),
                # A non-terminal event after ANSWER_END won't happen in practice,
                # but in the test it triggers the interval-based retry of the
                # retained batch.
                StreamMessage(event=StreamEvents.TOKEN_COUNT, data={}),
            ]
        )
    )
    assert terminal == StreamEvents.ANSWER_END
    # The retry call must also have compact=True
    successful_calls = [c for c in dal.calls if c.get("compact") is True]
    assert len(successful_calls) >= 1, (
        f"Expected at least one compact=True call to succeed; calls={dal.calls}"
    )


def test_publisher_returns_none_when_events_unsaved():
    """If all flush attempts return None, consume() must return None
    to signal the caller that the terminal batch was lost."""

    class _AlwaysNoneDal(_FakeDal):
        def post_conversation_events(self, **kwargs):
            self.calls.append(kwargs)
            return None

    dal = _AlwaysNoneDal()
    pub = ConversationEventPublisher(
        dal=dal,
        conversation_id="c1",
        assignee="h1",
        request_sequence=1,
        batch_interval_seconds=60.0,
    )
    terminal = pub.consume(
        _stream(
            [
                StreamMessage(event=StreamEvents.ANSWER_END, data={"content": "done"}),
            ]
        )
    )
    # consume() must return None because events are still unsaved
    assert terminal is None
