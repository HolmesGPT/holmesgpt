"""Shared utilities for extracting cost and token usage from LLM responses."""

import logging
from typing import NamedTuple

from litellm.types.utils import ModelResponse


class LLMResponseUsage(NamedTuple):
    """Raw cost and token data extracted from an LLM response."""

    cost: float
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int


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
    except (AttributeError, TypeError, KeyError):
        logging.debug("Could not extract token usage from LLM response")

    return LLMResponseUsage(
        cost=cost,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
