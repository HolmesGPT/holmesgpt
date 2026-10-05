"""compact_if_necessary end to end with a fake provider that enforces its context window (ROB-1519)."""

import pytest

from holmes.core.truncation.input_context_window_limiter import (
    CompactionInsufficientError,
    check_compaction_needed,
    compact_if_necessary,
)
from holmes.utils.stream import StreamEvents
from tests.core.truncation.test_compaction import (
    _TOOLS,
    CharCountingFakeLLM,
    _make_response,
    _oversized_history,
)


def test_oversized_conversation_is_compacted_instead_of_failing():
    """The ticket's case: the history plus the output reserve exceeds the window and
    the provider rejects anything that does not fit. Compaction must still succeed."""
    llm = CharCountingFakeLLM(
        [_make_response(content="THE SUMMARY")], accept_up_to=10_000 - 2_000
    )
    history = _oversized_history()

    assert check_compaction_needed(llm, history, _TOOLS) is not None  # type: ignore
    result = compact_if_necessary(llm, history, _TOOLS)  # type: ignore

    assert result.conversation_history_compacted is True
    assert result.tokens.total_tokens + result.maximum_output_token <= result.max_context_size
    assert "THE SUMMARY" in result.messages[1]["content"]
    assert result.messages[-1] == history[-1]
    compacted = next(e for e in result.events if e.event == StreamEvents.CONVERSATION_HISTORY_COMPACTED)
    assert compacted.data["metadata"]["input_truncated"] is True
    assert compacted.data["metadata"]["fallback_used"] is False


def test_oversized_conversation_recovers_via_fallback_budget():
    """The provider counts more tokens than we do, so the first fitted request is
    still rejected; a smaller fallback budget gets the summary through."""
    llm = CharCountingFakeLLM(
        [_make_response(content="THE SUMMARY")], accept_up_to=3_000
    )
    result = compact_if_necessary(llm, _oversized_history(), _TOOLS)  # type: ignore

    assert result.conversation_history_compacted is True
    compacted = next(e for e in result.events if e.event == StreamEvents.CONVERSATION_HISTORY_COMPACTED)
    assert compacted.data["metadata"]["fallback_used"] is True
    assert compacted.data["metadata"]["input_truncated"] is True
    assert "too long" in compacted.data["metadata"]["fallback_reason"]


def test_still_raises_when_provider_rejects_every_attempt():
    """If no attempt is accepted the user still gets the explicit error."""
    llm = CharCountingFakeLLM([], accept_up_to=10)
    with pytest.raises(CompactionInsufficientError) as exc:
        compact_if_necessary(llm, _oversized_history(), _TOOLS)  # type: ignore
    assert "Please start a new conversation" in str(exc.value)
    assert len(llm.calls) == 3


def test_small_conversation_is_not_compacted():
    """Below the threshold nothing is sent to the LLM."""
    llm = CharCountingFakeLLM([], context_window=100_000)
    history = _oversized_history(tool_result_chars=(400,))
    assert check_compaction_needed(llm, history, _TOOLS) is None  # type: ignore
    result = compact_if_necessary(llm, history, _TOOLS)  # type: ignore
    assert result.conversation_history_compacted is False
    assert result.messages == history
    assert llm.calls == []
