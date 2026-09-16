"""Tests for OrcaRouter provider support in DefaultLLM.

OrcaRouter is an OpenAI-compatible AI gateway. LiteLLM has no native
`orcarouter/` provider prefix, so DefaultLLM rewrites `orcarouter/<model>`
to `openai/<model>` and routes it to OrcaRouter's base URL (mirroring how
Robusta-hosted models are rewritten in get_litellm_corrected_name_for_robusta_ai).
"""
from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.core.llm import (
    ORCAROUTER_API_BASE,
    DefaultLLM,
    ModelEntry,
    _litellm_name_for_entry,
)


def _mock_model_response() -> ModelResponse:
    return ModelResponse(
        id="chatcmpl-test",
        choices=[
            Choices(
                index=0,
                message=Message(role="assistant", content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        model="test-model",
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _make_llm(model: str = "orcarouter/anthropic/claude-sonnet-4.5", **attrs) -> DefaultLLM:
    """Build a DefaultLLM bypassing __init__/check_llm so we can control attrs."""
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.model = model
    llm.api_key = "sk-orca-test"
    llm.api_base = None
    llm.api_version = None
    llm.args = {}
    llm.tracer = None
    llm.name = None
    llm.is_robusta_model = False
    llm.max_context_size = None
    for k, v in attrs.items():
        setattr(llm, k, v)
    return llm


class TestCheckLLMOrcaRouter:
    """check_llm must accept `orcarouter/` models as OpenAI-compatible without
    letting litellm.get_llm_provider reject the unknown prefix."""

    def test_check_llm_orcarouter_accepts_with_api_key(self):
        llm = _make_llm()
        # Should not raise even though litellm has no `orcarouter/` provider.
        llm.check_llm(
            model=llm.model,
            api_key="sk-orca-test",
            api_base=ORCAROUTER_API_BASE,
            api_version=None,
        )

    def test_check_llm_orcarouter_missing_key_still_raises(self):
        llm = _make_llm()
        with pytest.raises(
            Exception,
            match="requires the following environment variables",
        ):
            llm.check_llm(
                model=llm.model,
                api_key=None,
                api_base=ORCAROUTER_API_BASE,
                api_version=None,
            )

    def test_check_llm_orcarouter_does_not_call_get_llm_provider(self):
        llm = _make_llm()
        with patch("holmes.core.llm.litellm.get_llm_provider") as mock_get_provider:
            llm.check_llm(
                model=llm.model,
                api_key="sk-orca-test",
                api_base=ORCAROUTER_API_BASE,
                api_version=None,
            )
        mock_get_provider.assert_not_called()


class TestCompletionOrcaRouter:
    """completion must rewrite `orcarouter/` models to `openai/` and default
    the base URL to OrcaRouter's OpenAI-compatible endpoint."""

    def test_completion_rewrites_model_and_defaults_base_url(self):
        llm = _make_llm()
        with patch("holmes.core.llm.litellm.completion", return_value=_mock_model_response()) as mock:
            llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock.call_args.kwargs
        assert kwargs["model"] == "openai/anthropic/claude-sonnet-4.5"
        assert kwargs["base_url"] == ORCAROUTER_API_BASE
        assert kwargs["api_key"] == "sk-orca-test"

    def test_completion_respects_explicit_api_base(self):
        llm = _make_llm(api_base="https://custom.orcarouter.example/v1")
        with patch("holmes.core.llm.litellm.completion", return_value=_mock_model_response()) as mock:
            llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock.call_args.kwargs
        assert kwargs["base_url"] == "https://custom.orcarouter.example/v1"
        assert kwargs["model"] == "openai/anthropic/claude-sonnet-4.5"

    def test_non_orcarouter_model_unchanged(self):
        llm = _make_llm(model="openai/gpt-4o", api_base=None)
        with patch("holmes.core.llm.litellm.completion", return_value=_mock_model_response()) as mock:
            llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock.call_args.kwargs
        assert kwargs["model"] == "openai/gpt-4o"
        assert kwargs["base_url"] is None


class TestLitellmNameForEntry:
    """_litellm_name_for_entry must register OrcaRouter pricing under the same
    `openai/` name the completion call actually uses."""

    def test_orcarouter_entry_uses_corrected_litellm_name(self):
        entry = ModelEntry(
            model="orcarouter/anthropic/claude-sonnet-4.5",
        )
        assert _litellm_name_for_entry(entry) == "openai/anthropic/claude-sonnet-4.5"

    def test_non_orcarouter_entry_unchanged(self):
        entry = ModelEntry(model="openai/gpt-4o")
        assert _litellm_name_for_entry(entry) == "openai/gpt-4o"
