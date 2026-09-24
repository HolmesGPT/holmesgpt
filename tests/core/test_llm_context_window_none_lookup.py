"""Custom-priced models must not break context-window / max-output lookups.

When a model_list.yaml entry carries input_cost_per_token / output_cost_per_token,
Holmes registers it with litellm.register_model(). Once litellm normalizes that
entry (e.g. after a get_model_info() call), litellm.model_cost[name] is a full
ModelInfo dict where max_input_tokens and max_output_tokens are present but None.
The lookups must treat None like a missing entry and fall back, instead of
returning None and crashing callers such as Config._get_llm's token formatting.
"""

from unittest.mock import patch

from holmes.core.llm import FALLBACK_CONTEXT_WINDOW_SIZE, DefaultLLM


def _make_llm(model: str) -> DefaultLLM:
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.model = model
    llm.api_key = None
    llm.api_base = None
    llm.api_version = None
    llm.args = {}
    llm.tracer = None
    llm.name = None
    llm.is_robusta_model = False
    llm.max_context_size = None
    return llm


_NORMALIZED_PRICED_ENTRY = {
    "input_cost_per_token": 2.34e-06,
    "output_cost_per_token": 1.17e-05,
    "litellm_provider": "openai",
    "mode": "chat",
    "max_tokens": None,
    "max_input_tokens": None,
    "max_output_tokens": None,
}


def test_context_window_falls_back_when_max_input_tokens_is_none():
    model = "openai/moonshotai/kimi-k3-test"
    with patch.dict(
        "litellm.model_cost", {model: dict(_NORMALIZED_PRICED_ENTRY)}, clear=False
    ):
        llm = _make_llm(model)
        assert llm.get_context_window_size() == FALLBACK_CONTEXT_WINDOW_SIZE


def test_max_output_tokens_falls_back_when_max_output_tokens_is_none():
    model = "openai/moonshotai/kimi-k3-test"
    with patch.dict(
        "litellm.model_cost", {model: dict(_NORMALIZED_PRICED_ENTRY)}, clear=False
    ):
        llm = _make_llm(model)
        result = llm.get_maximum_output_token()
        assert isinstance(result, int)
        assert result == max(64000, FALLBACK_CONTEXT_WINDOW_SIZE * 12 // 100)


def test_real_max_tokens_still_honored():
    model = "openai/priced-with-limits-test"
    entry = dict(_NORMALIZED_PRICED_ENTRY)
    entry["max_input_tokens"] = 128000
    entry["max_output_tokens"] = 16000
    with patch.dict("litellm.model_cost", {model: entry}, clear=False):
        llm = _make_llm(model)
        assert llm.get_context_window_size() == 128000
        assert llm.get_maximum_output_token() == 16000
