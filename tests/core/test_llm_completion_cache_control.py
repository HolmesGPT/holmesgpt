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

    def test_gemini_completion_still_forwards_messages_and_model(self, mock_completion):
        """Skipping the cache hint must not drop anything else from the call."""
        llm = _make_llm("gemini/gemini-3.1-pro-preview")
        messages = [{"role": "user", "content": "hello"}]
        llm.completion(messages=messages, temperature=0.3)
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["model"] == "gemini/gemini-3.1-pro-preview"
        assert kwargs["messages"] == messages
        assert kwargs["temperature"] == 0.3

    def test_second_breakpoint_on_previous_user_message(self, mock_completion):
        """Multi-turn requests get a second breakpoint on the second-to-last user
        message so providers with a limited cache-lookback window (~20 content
        blocks on Anthropic/Bedrock) still find the previously cached prefix
        after long parallel tool-call bursts and on compaction requests.
        """
        llm = _make_llm("bedrock/anthropic.claude-sonnet-4-20250514-v1:0")
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},  # index 1: previous request's boundary
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
            {"role": "user", "content": "summarize the conversation"},
        ]
        llm.completion(messages=messages)
        kwargs = mock_completion.call_args.kwargs
        assert kwargs.get("cache_control_injection_points") == [
            {"location": "message", "index": 1},
            {"location": "message", "index": -1},
        ]

    def test_second_breakpoint_on_multi_turn_chat(self, mock_completion):
        """A follow-up turn breaks on the previous turn's user message (where the
        previous request's cache entry ends) plus the new last message."""
        llm = _make_llm("bedrock/anthropic.claude-sonnet-4-20250514-v1:0")
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        llm.completion(messages=messages)
        kwargs = mock_completion.call_args.kwargs
        assert kwargs.get("cache_control_injection_points") == [
            {"location": "message", "index": 1},
            {"location": "message", "index": -1},
        ]
