"""Tests for prompt-caching misconfiguration detection.

Holmes enables prompt caching for every non-Gemini model. In the agentic loop
the first LLM call creates the cache and every call after it should report
cache *read* tokens. A caching-enabled model that keeps returning 0 cache reads
on a large prompt is almost certainly misconfigured, and PromptCachingValidator
logs a single warning when it sees that pattern - without false-positiving on
the first call, small prompts, or models where caching is disabled.
"""

import logging

from holmes.core.llm import DefaultLLM
from holmes.core.llm_usage import (
    PROMPT_CACHING_MIN_PROMPT_TOKENS,
    PromptCachingValidator,
)

LARGE = PROMPT_CACHING_MIN_PROMPT_TOKENS + 100
SMALL = PROMPT_CACHING_MIN_PROMPT_TOKENS - 1

WARNING_FRAGMENT = "Prompt caching does not appear to be working"


def _warnings(caplog) -> list[str]:
    return [r.message for r in caplog.records if r.levelno == logging.WARNING]


def _make_llm(model: str) -> DefaultLLM:
    """Build a DefaultLLM bypassing __init__ so we can control self.model."""
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.model = model
    llm.is_robusta_model = False
    return llm


class TestWarns:
    def test_warns_when_caching_enabled_model_never_reads_cache(self, caplog):
        v = PromptCachingValidator(model="anthropic/claude-sonnet-4-5", caching_enabled=True)
        with caplog.at_level(logging.WARNING):
            v.record(prompt_tokens=LARGE, cached_tokens=0)  # first call creates cache
            v.record(prompt_tokens=LARGE, cached_tokens=0)  # 2nd call: expected a read
        warnings = _warnings(caplog)
        assert len(warnings) == 1
        assert WARNING_FRAGMENT in warnings[0]
        assert "anthropic/claude-sonnet-4-5" in warnings[0]

    def test_treats_none_cached_tokens_as_no_cache(self, caplog):
        """Providers that don't report the field at all (None) count as 0 reads."""
        v = PromptCachingValidator(model="openai/gpt-4o", caching_enabled=True)
        with caplog.at_level(logging.WARNING):
            v.record(prompt_tokens=LARGE, cached_tokens=None)
            v.record(prompt_tokens=LARGE, cached_tokens=None)
        assert len(_warnings(caplog)) == 1

    def test_warns_for_opaque_deployment_name_hiding_anthropic(self, caplog):
        """The real-world case: an Azure AI / Bedrock deployment whose name gives
        no hint it's Anthropic underneath, with caching misconfigured. The warning
        must still fire because the gate is non-Gemini, not 'is anthropic'.
        """
        llm = _make_llm("azure/my-company-prod-deployment")
        v = PromptCachingValidator(
            model=llm.model, caching_enabled=llm.is_prompt_caching_enabled()
        )
        with caplog.at_level(logging.WARNING):
            v.record(prompt_tokens=LARGE, cached_tokens=0)
            v.record(prompt_tokens=LARGE, cached_tokens=0)
        warnings = _warnings(caplog)
        assert len(warnings) == 1
        assert "azure/my-company-prod-deployment" in warnings[0]

    def test_warns_only_once_per_investigation(self, caplog):
        v = PromptCachingValidator(model="openai/gpt-4o", caching_enabled=True)
        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                v.record(prompt_tokens=LARGE, cached_tokens=0)
        assert len(_warnings(caplog)) == 1


class TestDoesNotWarn:
    def test_first_call_alone_never_warns(self, caplog):
        """The first call creates the cache - 0 reads there is expected."""
        v = PromptCachingValidator(model="anthropic/claude-sonnet-4-5", caching_enabled=True)
        with caplog.at_level(logging.WARNING):
            v.record(prompt_tokens=LARGE, cached_tokens=0)
        assert _warnings(caplog) == []

    def test_no_warning_when_cache_reads_present(self, caplog):
        v = PromptCachingValidator(model="anthropic/claude-sonnet-4-5", caching_enabled=True)
        with caplog.at_level(logging.WARNING):
            v.record(prompt_tokens=LARGE, cached_tokens=0)
            v.record(prompt_tokens=LARGE, cached_tokens=LARGE - 50)  # cache hit
        assert _warnings(caplog) == []

    def test_caching_confirmed_then_a_miss_does_not_warn(self, caplog):
        """Once caching is observed working, later misses (e.g. TTL expiry) are ignored."""
        v = PromptCachingValidator(model="anthropic/claude-sonnet-4-5", caching_enabled=True)
        with caplog.at_level(logging.WARNING):
            v.record(prompt_tokens=LARGE, cached_tokens=0)
            v.record(prompt_tokens=LARGE, cached_tokens=500)  # confirmed working
            v.record(prompt_tokens=LARGE, cached_tokens=0)  # later miss
        assert _warnings(caplog) == []

    def test_small_prompts_never_warn(self, caplog):
        """Prompts below the provider minimum are never cached - 0 reads is fine."""
        v = PromptCachingValidator(model="anthropic/claude-sonnet-4-5", caching_enabled=True)
        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                v.record(prompt_tokens=SMALL, cached_tokens=0)
        assert _warnings(caplog) == []

    def test_caching_disabled_model_never_warns(self, caplog):
        """Models where Holmes disables caching (e.g. Gemini) must not warn."""
        v = PromptCachingValidator(model="gemini/gemini-3.1-pro-preview", caching_enabled=False)
        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                v.record(prompt_tokens=LARGE, cached_tokens=0)
        assert _warnings(caplog) == []


class TestIsPromptCachingEnabled:
    """The validator's caching_enabled input comes from llm.is_prompt_caching_enabled(),
    which must stay in sync with completion()'s cache-hint gating (non-Gemini only).
    """

    def test_non_gemini_models_enabled(self):
        for model in [
            "anthropic/claude-sonnet-4-5",
            "gpt-5.4",
            "openai/gpt-4o",
            "azure/gpt-4.1",
            "bedrock/anthropic.claude-sonnet-4-20250514-v1:0",
            "vertex_ai/claude-3-5-sonnet",
        ]:
            assert _make_llm(model).is_prompt_caching_enabled() is True, model

    def test_opaque_custom_deployment_names_enabled(self):
        """Azure AI / Bedrock deployments often have custom names that hide the
        underlying Anthropic model. Detection is negative (non-Gemini), not based
        on recognizing the model as Anthropic, so these still get the warning.
        """
        for model in [
            "azure/my-company-prod-deployment",  # custom Azure AI deployment name
            "azure_ai/team-llm-v2",
            "bedrock/arn:aws:bedrock:us-east-1:123456789:provisioned-model/abc123",
            "some-internal-gateway-model",  # opaque proxy/gateway name
        ]:
            assert _make_llm(model).is_prompt_caching_enabled() is True, model

    def test_gemini_models_disabled(self):
        for model in [
            "gemini/gemini-3.1-pro-preview",
            "gemini/gemini-1.5-pro",
            "vertex_ai/gemini-2.0-flash",
            "vertex_ai_beta/gemini-2.5-pro",
        ]:
            assert _make_llm(model).is_prompt_caching_enabled() is False, model
