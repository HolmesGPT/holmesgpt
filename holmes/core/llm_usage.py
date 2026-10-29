"""Shared utilities for extracting cost and token usage from LLM responses."""

import logging
from typing import Optional

from litellm.types.utils import ModelResponse
from pydantic import BaseModel

from holmes.core.llm_rate_limit import iter_exception_chain, retry_counts


def _extract_detail_field(details: object, field: str) -> Optional[int]:
    """Extract an optional int field from a token-details object or dict.

    Returns None when the provider did not supply the metric (key absent
    or value is None).  Returns the int value (including 0) when the
    provider explicitly reported it.
    """
    if isinstance(details, dict):
        val = details.get(field)
    else:
        val = getattr(details, field, None)
    if val is None:
        return None
    return int(val)


def extract_usage_from_response(response: ModelResponse) -> dict:
    """Extract cost and token usage from a litellm ModelResponse.

    Handles missing attributes gracefully and returns zeros for any
    values that cannot be extracted.

    Args:
        response: A litellm ModelResponse or similar object.

    Returns:
        Dict with keys: cost, total_tokens, prompt_tokens,
        completion_tokens, cached_tokens, reasoning_tokens.
    """
    cost = 0.0
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    cached_tokens: Optional[int] = None
    cache_creation_tokens: Optional[int] = None
    reasoning_tokens = 0

    try:
        cost_value = (
            response._hidden_params.get("response_cost", 0)
            if hasattr(response, "_hidden_params")
            else 0
        )
        cost = float(cost_value) if cost_value is not None else 0.0
    except (AttributeError, TypeError, KeyError):
        logging.debug("Could not extract cost from LLM response")

    try:
        usage = getattr(response, "usage", None)
        if usage:
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)
            prompt_details = usage.get("prompt_tokens_details", None)
            if prompt_details:
                cached_tokens = _extract_detail_field(prompt_details, "cached_tokens")
            completion_details = usage.get("completion_tokens_details", None)
            if completion_details:
                reasoning_tokens = _extract_detail_field(completion_details, "reasoning_tokens") or 0
            # Anthropic/Bedrock prompt-cache writes (billed at a premium rate);
            # litellm surfaces them as a top-level usage field.
            cache_creation_tokens = usage.get("cache_creation_input_tokens", None)
    except (AttributeError, TypeError, KeyError):
        logging.debug("Could not extract token usage from LLM response")

    return {
        "cost": cost,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


class RequestStats(BaseModel):
    """Tracks cost and token usage for LLM calls.

    Supports ``+=`` for accumulation across iterations and approval rounds,
    and ``from_response()`` to extract stats from a raw litellm response.
    """

    total_cost: float = 0.0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: Optional[int] = None
    cache_creation_tokens: Optional[int] = None
    reasoning_tokens: int = 0
    max_completion_tokens_per_call: int = 0
    max_prompt_tokens_per_call: int = 0
    num_compactions: int = 0
    llm_rate_limit_retries: int = 0
    llm_rate_limit_wait_ms: int = 0

    @classmethod
    def from_response(cls, response) -> "RequestStats":
        """Build a single-response RequestStats from a litellm ModelResponse."""
        retries = retry_counts(response)
        try:
            raw = extract_usage_from_response(response)
        except (AttributeError, TypeError, KeyError) as e:
            logging.debug(f"Could not extract cost information: {e}")
            return cls(**retries)

        return cls(
            total_cost=raw["cost"],
            total_tokens=raw["total_tokens"],
            prompt_tokens=raw["prompt_tokens"],
            completion_tokens=raw["completion_tokens"],
            cached_tokens=raw["cached_tokens"],
            cache_creation_tokens=raw["cache_creation_tokens"],
            reasoning_tokens=raw["reasoning_tokens"],
            max_completion_tokens_per_call=raw["completion_tokens"],
            max_prompt_tokens_per_call=raw["prompt_tokens"],
            **retries,
        )

    def __iadd__(self, other: "RequestStats") -> "RequestStats":
        self.llm_rate_limit_retries += other.llm_rate_limit_retries
        self.llm_rate_limit_wait_ms += other.llm_rate_limit_wait_ms
        if other.total_tokens == 0 and other.total_cost == 0:
            return self
        self.total_cost += other.total_cost
        self.total_tokens += other.total_tokens
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        if other.cached_tokens is not None:
            self.cached_tokens = (self.cached_tokens or 0) + other.cached_tokens
        if other.cache_creation_tokens is not None:
            self.cache_creation_tokens = (
                self.cache_creation_tokens or 0
            ) + other.cache_creation_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.max_completion_tokens_per_call = max(
            self.max_completion_tokens_per_call, other.max_completion_tokens_per_call
        )
        self.max_prompt_tokens_per_call = max(
            self.max_prompt_tokens_per_call, other.max_prompt_tokens_per_call
        )
        self.num_compactions += other.num_compactions
        return self


_UNRECORDED_USAGE = "holmes_unrecorded_usage"
_REQUEST_USAGE = "holmes_request_usage"


def attach_unrecorded_usage(error: BaseException, usage: RequestStats) -> None:
    """Billed usage of the failing step that the caller has not counted yet
    (e.g. a failed compaction's requests)."""
    setattr(error, _UNRECORDED_USAGE, usage)


def unrecorded_usage(error: BaseException) -> Optional[RequestStats]:
    return _find_on_chain(error, _UNRECORDED_USAGE)


def failure_usage(error: BaseException) -> RequestStats:
    """What a failed step spent that its caller has not counted: the usage it
    attached, or else the failing LLM call's retry counters."""
    pending = unrecorded_usage(error)
    return pending if pending is not None else RequestStats(**retry_counts(error))


def attach_request_usage(error: BaseException, usage: RequestStats) -> None:
    """The whole request's usage up to the failure. The usage recorder only
    sees the raised error, so this is how it learns what was spent."""
    setattr(error, _REQUEST_USAGE, usage)


def request_usage(error: BaseException) -> Optional[RequestStats]:
    return _find_on_chain(error, _REQUEST_USAGE)


def _find_on_chain(error: BaseException, attribute: str) -> Optional[RequestStats]:
    for current in iter_exception_chain(error):
        usage = getattr(current, attribute, None)
        if isinstance(usage, RequestStats):
            return usage
    return None
