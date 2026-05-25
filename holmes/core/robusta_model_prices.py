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
``OpenAI_LLM.get_litellm_corrected_name_for_robusta_ai`` (typically
``openai/<model-id>``).

User-configured pricing in ``model_list.yaml`` overrides anything here --
``_init_models`` registers these defaults first and per-entry pricing
second.

Values are USD per token. Cache keys are Anthropic-specific and optional.

Maintainer note: entries below are stubs. Fill in authoritative numbers
from the Robusta pricing source-of-truth before relying on them in
production. Leaving the dict empty is safe: the mechanism still works
for users who set their own ``input_cost_per_token`` / ``output_cost_per_token``.
"""

from typing import Dict


ROBUSTA_MODEL_PRICES: Dict[str, Dict[str, float]] = {
    # Example shape -- replace with real Robusta-hosted model names + prices.
    #
    # "openai/opus-4.6": {
    #     "input_cost_per_token": 0.000003,
    #     "output_cost_per_token": 0.000015,
    #     "cache_creation_input_token_cost": 0.00000375,
    #     "cache_read_input_token_cost": 0.0000003,
    # },
}
