"""Shared utilities for extracting cost and token usage from LLM responses."""

import logging
from typing import Optional

from litellm.types.utils import ModelResponse
from pydantic import BaseModel


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

    return {
        "cost": cost,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
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
    reasoning_tokens: int = 0
    max_completion_tokens_per_call: int = 0
    max_prompt_tokens_per_call: int = 0
    num_compactions: int = 0

    @classmethod
    def from_response(cls, response) -> "RequestStats":
        """Build a single-response RequestStats from a litellm ModelResponse."""
        try:
            raw = extract_usage_from_response(response)
        except (AttributeError, TypeError, KeyError) as e:
            logging.debug(f"Could not extract cost information: {e}")
            return cls()

        return cls(
            total_cost=raw["cost"],
            total_tokens=raw["total_tokens"],
            prompt_tokens=raw["prompt_tokens"],
            completion_tokens=raw["completion_tokens"],
            cached_tokens=raw["cached_tokens"],
            reasoning_tokens=raw["reasoning_tokens"],
            max_completion_tokens_per_call=raw["completion_tokens"],
            max_prompt_tokens_per_call=raw["prompt_tokens"],
        )

    def __iadd__(self, other: "RequestStats") -> "RequestStats":
        if other.total_tokens == 0 and other.total_cost == 0:
            return self
        self.total_cost += other.total_cost
        self.total_tokens += other.total_tokens
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        if other.cached_tokens is not None:
            self.cached_tokens = (self.cached_tokens or 0) + other.cached_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.max_completion_tokens_per_call = max(
            self.max_completion_tokens_per_call, other.max_completion_tokens_per_call
        )
        self.max_prompt_tokens_per_call = max(
            self.max_prompt_tokens_per_call, other.max_prompt_tokens_per_call
        )
        self.num_compactions += other.num_compactions
        return self
