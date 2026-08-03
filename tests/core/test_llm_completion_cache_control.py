from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.core.llm import DefaultLLM


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
    llm.cache_control = None
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
            "bedrock/us.amazon.nova-pro-v1:0",
            "bedrock/us.amazon.nova-lite-v1:0",
            "bedrock/us.amazon.nova-micro-v1:0",
            "bedrock/eu-west-1.amazon.nova-pro-v1:0",
        ],
    )
    def test_bedrock_nova_models_skip_cache_control(self, mock_completion, model):
        llm = _make_llm(model)
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert "cache_control_injection_points" not in kwargs, (
            f"cache_control_injection_points must not be sent to {model}; "
            "Bedrock Nova rejects the cachePoint field it translates into."
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


class TestCacheControlOverride:
    """A per-model `cache_control` field in model_list.yaml must override the
    automatic per-route default: False suppresses the cache hint even for
    models that normally support it, True forces it for models that normally
    skip it (e.g. a Bedrock Nova model not covered by the default yet).
    """

    @pytest.mark.parametrize(
        "model",
        [
            "gemini/gemini-3.1-pro-preview",
            "bedrock/us.amazon.nova-pro-v1:0",
        ],
    )
    def test_cache_control_override_force_on(self, mock_completion, model):
        llm = _make_llm(model)
        llm.cache_control = True
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs.get("cache_control_injection_points") == [
            {"location": "message", "index": -1}
        ], f"cache_control: true must force the cache hint for {model}"

    @pytest.mark.parametrize(
        "model",
        [
            "openai/gpt-4o",
            "anthropic/claude-sonnet-4-5",
            "bedrock/anthropic.claude-sonnet-4-20250514-v1:0",
        ],
    )
    def test_cache_control_override_force_off(self, mock_completion, model):
        llm = _make_llm(model)
        llm.cache_control = False
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert "cache_control_injection_points" not in kwargs, (
            f"cache_control: false must suppress the cache hint for {model}"
        )

    def test_cache_control_popped_from_args(self):
        """The override flows into DefaultLLM.args from model_list.yaml and must
        be consumed by update_custom_args, never leaking into the litellm call."""
        llm = DefaultLLM.__new__(DefaultLLM)
        llm.args = {"cache_control": False, "temperature": 0.1}
        llm.update_custom_args()
        assert llm.cache_control is False
        assert "cache_control" not in llm.args
        assert llm.args == {"temperature": 0.1}
