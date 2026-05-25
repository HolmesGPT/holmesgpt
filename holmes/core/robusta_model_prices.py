"""Manual override layer for Robusta-hosted model pricing.

In the normal path HolmesGPT does **not** need entries here. When a
Robusta entry is loaded, ``LLMModelRegistry`` looks up the *real*
underlying model name (e.g. ``bedrock/us.anthropic.claude-opus-4-6-v1``)
in ``litellm.model_cost`` and copies the bundled pricing under the
corrected ``openai/...`` name automatically. That covers every model
LiteLLM already knows about, with no maintenance on our side.

This dict exists only as an escape hatch for the two cases the
auto-lookup can't handle:

1. LiteLLM doesn't ship pricing for the underlying model yet (brand-new
   release, private preview, custom internal endpoint).
2. The customer-facing price differs from the upstream LiteLLM number
   and that delta must be reflected in usage events.

Keys MUST be the exact name passed to ``litellm.completion()`` -- for
Robusta entries that is the output of
``OpenAI_LLM.get_litellm_corrected_name_for_robusta_ai``: the provider
prefix is stripped and replaced with ``openai/``. Values match the
LiteLLM cost-map schema (``input_cost_per_token``,
``output_cost_per_token``, optional Anthropic cache fields).

Entries here register *before* the auto-lookup runs, so a key in this
dict wins over whatever LiteLLM would have found.
"""

from typing import Dict


ROBUSTA_MODEL_PRICES: Dict[str, Dict[str, float]] = {
    # Example shape -- keep empty in normal operation. Auto-lookup against
    # litellm.model_cost handles Bedrock/Anthropic/etc. models without us
    # writing anything here.
    #
    # "openai/some-private-preview-model": {
    #     "input_cost_per_token": 0.000003,
    #     "output_cost_per_token": 0.000015,
    #     "cache_creation_input_token_cost": 0.00000375,
    #     "cache_read_input_token_cost": 0.0000003,
    # },
}
