"""Cache-token observability: prompt-cache reads/writes must flow from the
litellm response into RequestStats (and from there into the compaction event)."""

from litellm.types.utils import (
    Choices,
    Message,
    ModelResponse,
    PromptTokensDetailsWrapper,
    Usage,
)

from holmes.core.llm_usage import RequestStats


def _response_with_usage(**usage_kwargs) -> ModelResponse:
    """Build a minimal litellm ModelResponse carrying the given usage."""
    response = ModelResponse(
        choices=[Choices(message=Message(content="ok", role="assistant"))]
    )
    response.usage = Usage(**usage_kwargs)
    return response


def test_from_response_extracts_cache_read_and_write_tokens():
    """cached_tokens and cache_creation_tokens are read from litellm usage."""
    response = _response_with_usage(
        prompt_tokens=1000,
        completion_tokens=50,
        total_tokens=1050,
        prompt_tokens_details=PromptTokensDetailsWrapper(cached_tokens=800),
        cache_creation_input_tokens=150,
    )
    stats = RequestStats.from_response(response)
    assert stats.prompt_tokens == 1000
    assert stats.cached_tokens == 800
    assert stats.cache_creation_tokens == 150


def test_from_response_without_cache_fields():
    """Responses without cache usage leave the cache fields as None."""
    response = _response_with_usage(
        prompt_tokens=100, completion_tokens=10, total_tokens=110
    )
    stats = RequestStats.from_response(response)
    assert stats.cached_tokens is None
    assert stats.cache_creation_tokens is None


def test_iadd_accumulates_cache_fields():
    """+= accumulates cache reads/writes across calls (None-safe)."""
    total = RequestStats()
    total += RequestStats(
        total_tokens=100, cached_tokens=80, cache_creation_tokens=10
    )
    total += RequestStats(total_tokens=50)  # no cache info on this call
    total += RequestStats(
        total_tokens=200, cached_tokens=20, cache_creation_tokens=5
    )
    assert total.cached_tokens == 100
    assert total.cache_creation_tokens == 15
