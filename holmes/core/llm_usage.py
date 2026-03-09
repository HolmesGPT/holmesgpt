"""Shared utilities for extracting cost and token usage from LLM responses."""

import logging
from typing import NamedTuple, Optional

from litellm.types.utils import ModelResponse


class LLMResponseUsage(NamedTuple):
    """Raw cost and token data extracted from an LLM response."""

    cost: float
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: Optional[int]
    reasoning_tokens: int


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


def extract_usage_from_response(response: ModelResponse) -> LLMResponseUsage:
    """Extract cost and token usage from a litellm ModelResponse.

    Handles missing attributes gracefully and returns zeros for any
    values that cannot be extracted.

    Args:
        response: A litellm ModelResponse or similar object.

    Returns:
        LLMResponseUsage with cost and token counts.
    """
    cost = 0.0
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    cached_tokens: Optional[int] = None
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
    except (AttributeError, TypeError, KeyError):
        logging.debug("Could not extract token usage from LLM response")

    return LLMResponseUsage(
        cost=cost,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )
