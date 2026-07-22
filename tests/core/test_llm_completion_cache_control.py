import json
from unittest.mock import patch

import litellm
import openai
import pytest
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.core.llm import DefaultLLM


class _CaptureAndStop(Exception):
    """Raised from the mocked OpenAI SDK to halt the call once we've captured
    the fully-transformed outgoing request body."""


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


def _make_llm(model: str) -> DefaultLLM:
    """Build a DefaultLLM bypassing __init__/check_llm so we can control self.model."""
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


@pytest.fixture
def mock_completion():
    with patch("holmes.core.llm.litellm.completion") as mock:
        mock.return_value = _mock_model_response()
        yield mock


class TestCacheControlInjectionPoints:
    """Gemini rejects GenerateContent requests that combine CachedContent with
    system_instruction/tools/tool_config (the exact shape produced by litellm's
    cache_control_injection_points hook). The completion() helper must therefore
    skip that kwarg for Gemini and Vertex-AI Gemini routes while keeping it for
    every other provider that benefits from prompt caching (Anthropic, OpenAI,
    Bedrock, Azure, etc.).
    """

    @pytest.mark.parametrize(
        "model",
        [
            "gemini/gemini-3.1-pro-preview",
            "gemini/gemini-1.5-pro",
            "vertex_ai/gemini-2.0-flash",
            "vertex_ai_beta/gemini-2.5-pro",
        ],
    )
    def test_gemini_models_skip_cache_control(self, mock_completion, model):
        llm = _make_llm(model)
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert "cache_control_injection_points" not in kwargs, (
            f"cache_control_injection_points must not be sent to {model}; "
            "Gemini rejects CachedContent + system_instruction/tools/tool_config."
        )

    @pytest.mark.parametrize(
        "model",
        [
            "anthropic/claude-sonnet-4-5",
            "gpt-5.4",
            "openai/gpt-4o",
            "azure/gpt-4.1",
            "bedrock/anthropic.claude-sonnet-4-20250514-v1:0",
            "vertex_ai/claude-3-5-sonnet",
        ],
    )
    def test_non_gemini_models_keep_cache_control(self, mock_completion, model):
        llm = _make_llm(model)
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs.get("cache_control_injection_points") == [
            {"location": "message", "index": -1}
        ], f"cache_control_injection_points must be forwarded for {model}"

    def test_gemini_completion_still_forwards_messages_and_model(self, mock_completion):
        """Skipping the cache hint must not drop anything else from the call."""
        llm = _make_llm("gemini/gemini-3.1-pro-preview")
        messages = [{"role": "user", "content": "hello"}]
        llm.completion(messages=messages, temperature=0.3)
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["model"] == "gemini/gemini-3.1-pro-preview"
        assert kwargs["messages"] == messages
        assert kwargs["temperature"] == 0.3


class TestOpenAICompatibleEndpointPreservesCacheControl:
    """Regression guard for prompt caching against a Robusta-hosted AI gateway.

    Robusta models are sent with the ``openai/`` provider (see
    ``get_litellm_corrected_name_for_robusta_ai``) but reach an
    OpenAI-*compatible* gateway/proxy via ``api_base`` — an endpoint that DOES
    understand Anthropic ``cache_control`` markers. litellm < 1.90.0
    unconditionally stripped ``cache_control`` for the ``openai/`` provider, so
    the markers injected by ``cache_control_injection_points`` were silently
    removed before the request ever left Holmes and prompt caching never
    happened. litellm >= 1.90.0 preserves them for non-``openai.com`` hosts
    (``OpenAIGPTConfig._should_preserve_cache_control_for_endpoint``).

    These tests fail if the litellm pin is ever moved back below 1.90.0.
    """

    def _transform(self, api_base: str) -> str:
        config = litellm.OpenAIGPTConfig()
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "hi",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ]
        transformed = config.transform_request(
            model="claude-opus-4-8",
            messages=messages,
            optional_params={},
            litellm_params={"custom_llm_provider": "openai", "api_base": api_base},
            headers={},
        )
        return json.dumps(transformed)

    def test_cache_control_survives_for_custom_gateway(self):
        body = self._transform("https://llm.eu.robusta.dev/v1")
        assert "cache_control" in body, (
            "cache_control must survive the openai/ transform when the endpoint "
            "is an OpenAI-compatible gateway (non-openai.com api_base); requires "
            "litellm >= 1.90.0."
        )

    def test_cache_control_stripped_for_real_openai(self):
        body = self._transform("https://api.openai.com/v1")
        assert "cache_control" not in body, (
            "cache_control must still be stripped for real api.openai.com "
            "(OpenAI rejects the Anthropic-only marker)."
        )


class TestRobustaCompletionEmitsCacheControlToProxy:
    """End-to-end guard for the deployed topology Holmes -> relay -> LiteLLM proxy.

    This covers Holmes's own hop (Holmes -> the OpenAI-compatible endpoint it is
    pointed at, i.e. the relay/proxy). It drives the *real* ``DefaultLLM.completion``
    path — cache_control injection hook + ``openai/`` corrected name + provider
    ``transform_request`` — and captures the fully-transformed request body at the
    OpenAI SDK boundary (after every litellm transform has run). It asserts the
    Anthropic ``cache_control`` markers actually leave Holmes toward a custom
    (non-openai.com) gateway host.

    NOTE: the second hop (relay -> proxy) lives in the `relay` repo and calls
    ``litellm.completion`` itself (``relay/pkg/llm/llm_registry.py``), so it needs
    its own equivalent guard there and the same litellm >= 1.90.0 floor.
    """

    def _capture_outgoing_messages(self, api_base: str):
        captured: dict = {}

        def fake_create(_self, *args, **kwargs):
            captured["messages"] = kwargs.get("messages")
            raise _CaptureAndStop()

        llm = _make_llm("bedrock/anthropic.claude-sonnet-4-5")
        llm.is_robusta_model = True  # -> corrected name becomes openai/<model>
        llm.api_base = api_base
        llm.api_key = "sk-test"
        llm.args = {"num_retries": 0}

        with patch.object(
            openai.resources.chat.completions.Completions, "create", fake_create
        ):
            with pytest.raises(Exception):
                llm.completion(
                    messages=[{"role": "user", "content": "some context " * 20}]
                )
        return captured.get("messages")

    def test_cache_control_reaches_openai_compatible_gateway(self):
        messages = self._capture_outgoing_messages("https://llm.eu.robusta.dev/v1")
        assert messages is not None, "OpenAI SDK was never called"
        assert "cache_control" in json.dumps(messages), (
            "A Robusta model pointed at an OpenAI-compatible gateway must send "
            "Anthropic cache_control markers through to the endpoint; requires "
            "litellm >= 1.90.0."
        )

    def test_cache_control_stripped_when_pointed_at_real_openai(self):
        messages = self._capture_outgoing_messages("https://api.openai.com/v1")
        assert messages is not None, "OpenAI SDK was never called"
        assert "cache_control" not in json.dumps(messages), (
            "cache_control must still be stripped when the endpoint is real "
            "api.openai.com."
        )
