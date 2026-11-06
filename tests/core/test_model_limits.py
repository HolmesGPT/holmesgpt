"""Model limits and where they came from, as published in the HolmesStatus
heartbeat so the platform can flag models running on fallback limits."""

import threading
from unittest.mock import patch

import pytest

from holmes.core.llm import (
    FALLBACK_CONTEXT_WINDOW_SIZE,
    LLMModelRegistry,
    ModelEntry,
    resolve_model_limits,
)


@pytest.fixture(autouse=True)
def no_cost_map_refresh():
    # A miss would otherwise try to download a fresh cost map.
    with patch("holmes.core.llm.refresh_model_cost_map_if_stale", return_value=False):
        yield


def test_known_model_comes_from_litellm():
    limits = resolve_model_limits("openai/gpt-4o")
    assert limits.context_window == 128000
    assert limits.context_window_source == "litellm"
    assert limits.max_output_tokens == 16384
    assert limits.max_output_tokens_source == "litellm"


@pytest.mark.parametrize(
    "model",
    [
        "litellm_proxy/team-gpt4o",
        "openai/team-gpt4o",
        "azure/my-gpt4o-prod",
        "hosted_vllm/qwen-72b-prod",
    ],
)
def test_unknown_model_falls_back_to_defaults(model):
    limits = resolve_model_limits(model)
    assert limits.context_window == FALLBACK_CONTEXT_WINDOW_SIZE
    assert limits.context_window_source == "default"
    assert limits.max_output_tokens == 64000
    assert limits.max_output_tokens_source == "default"


def test_config_overrides_are_reported_as_config():
    limits = resolve_model_limits(
        "litellm_proxy/team-gpt4o",
        custom_args={"max_context_size": 128000},
        args={"max_tokens": 16384},
    )
    assert (limits.context_window, limits.context_window_source) == (128000, "config")
    assert (limits.max_output_tokens, limits.max_output_tokens_source) == (
        16384,
        "config",
    )


def test_max_completion_tokens_counts_as_config():
    limits = resolve_model_limits(
        "litellm_proxy/team-gpt4o", args={"max_completion_tokens": 8000}
    )
    assert (limits.max_output_tokens, limits.max_output_tokens_source) == (
        8000,
        "config",
    )
    # Only the output is set, so the context window is still a guess.
    assert limits.context_window_source == "default"


def test_zero_max_tokens_is_reported_as_config():
    # completion() sends an explicit 0, so the status must report it too.
    limits = resolve_model_limits("litellm_proxy/team-gpt4o", args={"max_tokens": 0})
    assert (limits.max_output_tokens, limits.max_output_tokens_source) == (0, "config")


def test_null_max_tokens_is_not_an_override():
    limits = resolve_model_limits("litellm_proxy/team-gpt4o", args={"max_tokens": None})
    assert limits.max_output_tokens_source == "default"


def test_env_overrides_are_reported_as_env():
    with patch("holmes.core.llm.OVERRIDE_MAX_CONTENT_SIZE", 50000), patch(
        "holmes.core.llm.OVERRIDE_MAX_OUTPUT_TOKEN", 4000
    ):
        limits = resolve_model_limits("litellm_proxy/team-gpt4o")
    assert (limits.context_window, limits.context_window_source) == (50000, "env")
    assert (limits.max_output_tokens, limits.max_output_tokens_source) == (4000, "env")


def test_registry_reports_limits_per_model_key():
    registry = LLMModelRegistry.__new__(LLMModelRegistry)
    registry._lock = threading.RLock()
    registry._llms = {
        "team-gpt4o": ModelEntry.model_validate({"model": "litellm_proxy/team-gpt4o"}),
        "team-gpt4o-fixed": ModelEntry.model_validate(
            {
                "model": "litellm_proxy/team-gpt4o",
                "max_tokens": 16384,
                "custom_args": {"max_context_size": 128000},
            }
        ),
        "gpt-4o": ModelEntry.model_validate({"model": "openai/gpt-4o"}),
    }

    limits = registry.get_models_limits()

    assert limits["team-gpt4o"].context_window_source == "default"
    assert limits["team-gpt4o-fixed"].context_window == 128000
    assert limits["team-gpt4o-fixed"].max_output_tokens == 16384
    assert limits["gpt-4o"].context_window_source == "litellm"


def test_registry_skips_only_the_model_that_fails():
    registry = LLMModelRegistry.__new__(LLMModelRegistry)
    registry._lock = threading.RLock()
    registry._llms = {
        "bad": ModelEntry.model_validate(
            {"model": "openai/gpt-4o", "custom_args": {"max_context_size": "lots"}}
        ),
        "gpt-4o": ModelEntry.model_validate({"model": "openai/gpt-4o"}),
    }

    limits = registry.get_models_limits()

    assert list(limits) == ["gpt-4o"]
