from unittest.mock import MagicMock, patch

import litellm
import pytest

from holmes.core.llm import ContextWindowUsage
from holmes.core.truncation.input_context_window_limiter import (
    CompactionInsufficientError,
    compact_if_necessary,
)

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
    error = litellm.RateLimitError(
        message="slow down", llm_provider="openai", model="m"
    )
    error.llm_rate_limit_retries = 3  # type: ignore[attr-defined]
    error.llm_rate_limit_wait_ms = 12000  # type: ignore[attr-defined]
    return error


@pytest.fixture(autouse=True)
def _compaction_on():
    with patch(f"{MODULE}.ENABLE_CONVERSATION_HISTORY_COMPACTION", True), patch(
        f"{MODULE}.get_context_window_compaction_threshold_pct", return_value=95
    ):
        yield


def test_rate_limited_compaction_is_skipped_while_history_fits():
    messages = [{"role": "user", "content": "hi"}]
    # 900 + 100 output crosses the 95% threshold but fits the 1000 window
    with patch(f"{MODULE}.compact_conversation_history", side_effect=_rate_limited()):
        result = compact_if_necessary(llm=_llm(900), messages=messages, tools=None)

    assert result.messages == messages
    assert result.conversation_history_compacted is False
    assert result.compaction_usage.llm_rate_limit_retries == 3
    assert result.compaction_usage.llm_rate_limit_wait_ms == 12000


def test_rate_limited_compaction_fails_the_turn_when_history_does_not_fit():
    with patch(f"{MODULE}.compact_conversation_history", side_effect=_rate_limited()):
        with pytest.raises(litellm.RateLimitError):
            compact_if_necessary(
                llm=_llm(950), messages=[{"role": "user", "content": "hi"}], tools=None
            )


def test_other_compaction_errors_still_propagate():
    with patch(f"{MODULE}.compact_conversation_history", side_effect=ValueError("bug")):
        with pytest.raises(ValueError):
            compact_if_necessary(
                llm=_llm(900), messages=[{"role": "user", "content": "hi"}], tools=None
            )


def test_history_too_big_without_rate_limit_is_still_insufficient():
    messages = [{"role": "user", "content": "hi"}]
    result = MagicMock(messages_after_compaction=messages, usage=None)
    with patch(f"{MODULE}.compact_conversation_history", return_value=result):
        with pytest.raises(CompactionInsufficientError):
            compact_if_necessary(llm=_llm(950), messages=messages, tools=None)
