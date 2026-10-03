"""Shared utilities for extracting cost and token usage from LLM responses."""

import logging
from typing import Optional

from litellm.types.utils import ModelResponse
from pydantic import BaseModel


# Minimum prompt size (in tokens) before prompt caching is expected to kick in.
# Anthropic requires >=1024 tokens to cache (2048 for some smaller models) and
# OpenAI only auto-caches prompts longer than 1024 tokens. Below this, zero
# cache reads is normal and must not be treated as a misconfiguration.
PROMPT_CACHING_MIN_PROMPT_TOKENS = 1024


class PromptCachingValidator:
    """Detects when prompt caching looks misconfigured during an agentic loop.

    Holmes runs an agentic loop that makes many sequential LLM calls sharing a
    large, growing prefix (system prompt + tool schemas + prior turns). With
    prompt caching working, the first call *creates* the cache and every call
    after it should report cache *read* tokens. When a caching-enabled model
    repeatedly reports zero cache reads on a large prompt, caching is almost
    certainly not working - a common symptom of a model/deployment that hasn't
    been set up for prompt caching. That silently inflates cost and latency.

    This logs a single warning per investigation when that pattern is seen,
    while avoiding false positives:

    - models where Holmes doesn't enable caching (e.g. Gemini) are skipped
    - the first call is skipped (it creates the cache; reads start on call 2)
    - prompts below the provider minimum (~1024 tokens) are skipped
    - once a non-zero cache read is observed, caching is confirmed working and
      no further checks are made
    """

    def __init__(self, model: str, caching_enabled: bool):
        self.model = model
        self.caching_enabled = caching_enabled
        self._calls_seen = 0
        self._done = False

    def record(self, prompt_tokens: int, cached_tokens: Optional[int]) -> None:
        """Inspect one LLM call's usage and warn once if caching looks broken."""
        if self._done or not self.caching_enabled:
            return

        self._calls_seen += 1
        # The first call of a conversation creates the cache; cache *reads*
        # only appear from the second call onward, so don't judge the first.
        if self._calls_seen < 2:
            return

        if cached_tokens:  # non-zero -> caching is confirmed working
            self._done = True
            return

        # Prompts below the provider minimum are never cached, so zero reads
        # here is expected rather than a sign of misconfiguration.
        if prompt_tokens < PROMPT_CACHING_MIN_PROMPT_TOKENS:
            return

        # Caching enabled, past the first call, large prompt, yet 0 cache reads.
        self._done = True
        logging.warning(
            "Prompt caching does not appear to be working for model '%s': "
            "%d prompt tokens were sent on a repeated request but the provider "
            "reported 0 cached tokens. This usually means prompt caching is not "
            "enabled for your model or deployment, which significantly increases "
            "cost and latency. Verify that your provider/model supports prompt "
            "caching and that it is enabled for your account.",
            self.model,
            prompt_tokens,
        )


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
            cache_creation_tokens=raw["cache_creation_tokens"],
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
