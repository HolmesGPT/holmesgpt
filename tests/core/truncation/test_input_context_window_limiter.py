"""compact_if_necessary end to end with a fake provider that enforces its context window (ROB-1519)."""

from unittest.mock import MagicMock, patch

import litellm
import pytest

from holmes.core import llm_rate_limit
from holmes.core.llm import ContextWindowUsage
from holmes.core.truncation.compaction import CompactionResult
from holmes.core.truncation.input_context_window_limiter import (
    CompactionInsufficientError,
    check_compaction_needed,
    compact_if_necessary,
)
from holmes.utils.stream import StreamEvents
from tests.core.truncation.fake_llm import CharCountingFakeLLM, oversized_history

_TOOLS = [{"type": "function", "function": {"name": "kubectl_get", "parameters": {"type": "object"}}}]


def test_oversized_conversation_is_compacted_instead_of_failing():
    """The ticket's case: the history plus the output reserve exceeds the window and
    the provider rejects anything that does not fit. Compaction must still succeed."""
    llm = CharCountingFakeLLM([], accept_up_to=10_000 - 2_000)
    history = oversized_history()

    assert check_compaction_needed(llm, history, _TOOLS) is not None  # type: ignore
    result = compact_if_necessary(llm, history, _TOOLS)  # type: ignore

    assert result.conversation_history_compacted is True
    assert result.tokens.total_tokens + result.maximum_output_token <= result.max_context_size
    assert f"SUMMARY {len(llm.calls)}" in result.messages[1]["content"]
    assert result.messages[-1] == history[-1]
    compacted = next(e for e in result.events if e.event == StreamEvents.CONVERSATION_HISTORY_COMPACTED)
    assert compacted.data["metadata"]["summarization_requests"] > 1
    assert result.compaction_overflowing_tool_call_ids == ["c0", "c1"]


def test_oversized_conversation_recovers_via_smaller_chunks():
    """The provider counts more tokens than we do, so full-size chunks are
    rejected; smaller chunks get the summary through."""
    llm = CharCountingFakeLLM([], accept_up_to=3_000)
    result = compact_if_necessary(llm, oversized_history(), _TOOLS)  # type: ignore

    assert result.conversation_history_compacted is True
    compacted = next(e for e in result.events if e.event == StreamEvents.CONVERSATION_HISTORY_COMPACTED)
    assert compacted.data["metadata"]["fallback_used"] is True
    assert compacted.data["metadata"]["summarization_requests"] > 1


def test_still_raises_when_provider_rejects_every_attempt():
    """If no attempt is accepted the user still gets the explicit error."""
    llm = CharCountingFakeLLM([], accept_up_to=10)
    with pytest.raises(CompactionInsufficientError) as exc:
        compact_if_necessary(llm, oversized_history(), _TOOLS)  # type: ignore
    assert "Please start a new conversation" in str(exc.value)
    assert len(llm.calls) == 3


def test_small_conversation_is_not_compacted():
    """Below the threshold nothing is sent to the LLM."""
    llm = CharCountingFakeLLM([], context_window=100_000)
    history = oversized_history(tool_result_chars=(400,))
    assert check_compaction_needed(llm, history, _TOOLS) is None  # type: ignore
    result = compact_if_necessary(llm, history, _TOOLS)  # type: ignore
    assert result.conversation_history_compacted is False
    assert result.messages == history
    assert llm.calls == []


# --- Provider refusing compaction (ROB-1552) ---

MODULE = "holmes.core.truncation.input_context_window_limiter"


def _llm(total_tokens: int) -> MagicMock:
    llm = MagicMock()
    llm.count_tokens.return_value = ContextWindowUsage(
        total_tokens=total_tokens,
        system_tokens=0,
        tools_to_call_tokens=0,
        tools_tokens=0,
        user_tokens=total_tokens,
        assistant_tokens=0,
        other_tokens=0,
    )
    llm.get_context_window_size.return_value = 1000
    llm.get_maximum_output_token.return_value = 100
    return llm


def _rate_limited() -> litellm.RateLimitError:
    return litellm.RateLimitError(message="slow down", llm_provider="openai", model="m")


@pytest.fixture
def _compaction_on():
    with patch(f"{MODULE}.ENABLE_CONVERSATION_HISTORY_COMPACTION", True), patch(
        f"{MODULE}.get_context_window_compaction_threshold_pct", return_value=95
    ):
        yield


# 900 + 100 output crosses the 95% threshold but fits the 1000 window; 950 does not.
@pytest.mark.parametrize("tokens", [900, 950])
@pytest.mark.usefixtures("_compaction_on")
def test_rate_limited_compaction_fails_the_turn_with_the_provider_error(tokens):
    error = _rate_limited()
    with patch(f"{MODULE}.compact_conversation_history", side_effect=error):
        with pytest.raises(litellm.RateLimitError) as info:
            compact_if_necessary(
                llm=_llm(tokens), messages=[{"role": "user", "content": "hi"}], tools=None
            )
    assert info.value is error
    assert info.value.__cause__ is None


@pytest.mark.usefixtures("_compaction_on")
def test_other_compaction_errors_still_propagate():
    with patch(f"{MODULE}.compact_conversation_history", side_effect=ValueError("bug")):
        with pytest.raises(ValueError):
            compact_if_necessary(
                llm=_llm(900), messages=[{"role": "user", "content": "hi"}], tools=None
            )


@pytest.mark.usefixtures("_compaction_on")
def test_history_too_big_without_rate_limit_is_still_insufficient():
    messages = [{"role": "user", "content": "hi"}]
    result = MagicMock(messages_after_compaction=messages, usage=None)
    with patch(f"{MODULE}.compact_conversation_history", return_value=result):
        with pytest.raises(CompactionInsufficientError):
            compact_if_necessary(llm=_llm(950), messages=messages, tools=None)


@pytest.mark.parametrize("tokens", [900, 950])
@pytest.mark.usefixtures("_compaction_on")
def test_compaction_requests_share_one_budget(tokens):
    messages = [{"role": "user", "content": "hi"}]
    seen = {}

    def compact(original_conversation_history, **_kwargs):
        scope = llm_rate_limit._retry_scope.get()
        seen["per_call"] = scope.max_wait_seconds
        seen["shared"] = scope.shared_budget
        return CompactionResult(messages_after_compaction=original_conversation_history)

    with patch(f"{MODULE}.compact_conversation_history", side_effect=compact):
        try:
            compact_if_necessary(llm=_llm(tokens), messages=messages, tools=None)
        except CompactionInsufficientError:
            pass
    assert seen["per_call"] is None
    assert seen["shared"] is not None
