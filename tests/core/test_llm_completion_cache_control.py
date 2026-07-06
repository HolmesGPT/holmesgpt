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

def _make_openrouter_llm(model: str) -> DefaultLLM:
    llm = _make_llm(model)
    llm.api_base = "https://openrouter.ai/api/v1"
    return llm


class TestOpenRouterClaudeCacheControl:
    """Claude via OpenRouter is commonly configured as
    `openai/anthropic/claude-...`, but on litellm's OpenAI-compatible path
    every cache_control hint is silently stripped, so all calls were billed
    with zero prompt caching. completion() must reroute those requests
    through litellm's native openrouter/ provider (which forwards embedded
    markers) and mark the last message itself."""

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("openai/anthropic/claude-fable-5", "openrouter/anthropic/claude-fable-5"),
            ("openai/anthropic/claude-opus-4.8", "openrouter/anthropic/claude-opus-4.8"),
            ("openai/claude-sonnet-4-5", "openrouter/claude-sonnet-4-5"),
        ],
    )
    def test_reroutes_and_marks_last_message(self, mock_completion, model, expected):
        llm = _make_openrouter_llm(model)
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["model"] == expected
        assert "cache_control_injection_points" not in kwargs
        assert kwargs["messages"][-1]["content"] == [
            {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}
        ]

    def test_marks_last_text_block_of_multimodal_content(self, mock_completion):
        llm = _make_openrouter_llm("openai/anthropic/claude-fable-5")
        content = [
            {"type": "text", "text": "tool output"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ]
        llm.completion(messages=[{"role": "tool", "tool_call_id": "c1", "content": content}])
        sent = mock_completion.call_args.kwargs["messages"][-1]["content"]
        assert sent[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in sent[1]

    def test_does_not_mutate_caller_messages(self, mock_completion):
        llm = _make_openrouter_llm("openai/anthropic/claude-fable-5")
        messages = [{"role": "user", "content": "hi"}]
        llm.completion(messages=messages)
        assert messages == [{"role": "user", "content": "hi"}]

    def test_none_content_left_alone(self, mock_completion):
        llm = _make_openrouter_llm("openai/anthropic/claude-fable-5")
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": []},
        ]
        llm.completion(messages=messages)
        assert mock_completion.call_args.kwargs["messages"][-1]["content"] is None

    def test_openai_models_on_openrouter_unaffected(self, mock_completion):
        llm = _make_openrouter_llm("openai/gpt-4o")
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["model"] == "openai/gpt-4o"
        assert kwargs["messages"][-1]["content"] == "hi"
        assert kwargs.get("cache_control_injection_points") == [
            {"location": "message", "index": -1}
        ]

    def test_claude_on_non_openrouter_base_unaffected(self, mock_completion):
        llm = _make_llm("openai/anthropic/claude-fable-5")
        llm.api_base = "https://my-litellm-proxy.internal/v1"
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["model"] == "openai/anthropic/claude-fable-5"
        assert kwargs["messages"][-1]["content"] == "hi"


class TestGeminiCompletionForwarding:
    def test_gemini_completion_still_forwards_messages_and_model(self, mock_completion):
        """Skipping the cache hint must not drop anything else from the call."""
        llm = _make_llm("gemini/gemini-3.1-pro-preview")
        messages = [{"role": "user", "content": "hello"}]
        llm.completion(messages=messages, temperature=0.3)
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["model"] == "gemini/gemini-3.1-pro-preview"
        assert kwargs["messages"] == messages
        assert kwargs["temperature"] == 0.3
