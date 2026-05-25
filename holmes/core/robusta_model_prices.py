"""Default per-token pricing for Robusta-hosted LLM models.

LiteLLM's bundled cost map (``model_prices_and_context_window.json``) only
knows about first-party model names (``gpt-5``, ``claude-opus-4-5-...``).
When a Robusta-hosted variant is invoked, litellm cannot resolve a price
and writes ``response_cost=0`` into ``_hidden_params``, so Holmes emits a
``costs.total_cost`` of ``0`` in ``ANSWER_END`` usage events.

This dict registers fallback prices with ``litellm.register_model`` at
``LLMModelRegistry`` startup so those costs come out non-zero out of the
box. Keys MUST be the exact name passed to ``litellm.completion()`` -- for
Robusta entries that is the output of
``OpenAI_LLM.get_litellm_corrected_name_for_robusta_ai``: provider prefix
gets stripped and replaced with ``openai/`` (see ``holmes/core/llm.py``).
For example a Robusta entry with
``model="bedrock/us.anthropic.claude-opus-4-6-v1"`` becomes
``openai/us.anthropic.claude-opus-4-6-v1`` when passed to litellm.

User-configured pricing in ``model_list.yaml`` overrides anything here --
``_init_models`` registers these defaults first and per-entry pricing
second.

Values are USD per token. Cache keys are Anthropic-specific and optional.
Prices below match Anthropic's wholesale public pricing
(https://www.anthropic.com/pricing) as of 2026-05; Robusta passes those
through to customers at parity. Update when Anthropic revises pricing
or when Robusta starts charging a markup.
"""

from typing import Dict


# Anthropic Claude Opus 4.6 / 4.7 wholesale pricing (USD per token).
# Both generations are priced identically as of 2026-05.
_OPUS_4X_PRICING: Dict[str, float] = {
    "input_cost_per_token": 5e-06,  # $5  / MTok
    "output_cost_per_token": 2.5e-05,  # $25 / MTok
    "cache_creation_input_token_cost": 6.25e-06,  # $6.25 / MTok (5-min cache write)
    "cache_read_input_token_cost": 5e-07,  # $0.50 / MTok
}


ROBUSTA_MODEL_PRICES: Dict[str, Dict[str, float]] = {
    # Robusta hosts Opus 4.6/4.7 on AWS Bedrock us-region. Robusta entries
    # arrive with model="bedrock/us.anthropic.claude-opus-4-X-..." and get
    # rewritten to "openai/us.anthropic.claude-opus-4-X-..." before litellm
    # sees them; that rewritten string is the cost-map lookup key.
    #
    # Bedrock naming changed between generations: 4.6 keeps the historical
    # "-v1" suffix, 4.7 drops it. We register both 4.7 variants so the
    # backfill keeps working if Robusta's backend ever standardizes.
    "openai/us.anthropic.claude-opus-4-6-v1": _OPUS_4X_PRICING,
    "openai/us.anthropic.claude-opus-4-7": _OPUS_4X_PRICING,
    "openai/us.anthropic.claude-opus-4-7-v1": _OPUS_4X_PRICING,
}
