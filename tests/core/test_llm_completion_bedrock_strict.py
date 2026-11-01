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


def _make_llm(model: str, args: dict | None = None) -> DefaultLLM:
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.model = model
    llm.api_key = None
    llm.api_base = None
    llm.api_version = None
    llm.args = args or {}
    llm.tracer = None
    llm.name = None
    llm.is_robusta_model = False
    llm.max_context_size = None
    return llm


def _strict_tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "d",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"q": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
                "required": ["q"],
                "additionalProperties": False,
            },
        },
    }


@pytest.fixture
def mock_completion():
    with patch("holmes.core.llm.litellm.completion") as mock:
        mock.return_value = _mock_model_response()
        yield mock


def _sent_tools(mock_completion) -> list:
    return mock_completion.call_args.kwargs["tools"]


@pytest.mark.parametrize(
    "model,args",
    [
        ("bedrock/eu.anthropic.claude-opus-4-6-v1", {}),
        ("bedrock/converse/us.anthropic.claude-sonnet-4-5-20250929-v1:0", {}),
        ("us.anthropic.claude-opus-4-6-v1", {"custom_llm_provider": "bedrock"}),
    ],
)
def test_bedrock_tools_are_sent_without_strict(mock_completion, model, args):
    tools = [_strict_tool("a"), _strict_tool("b")]
    _make_llm(model, args).completion(
        messages=[{"role": "user", "content": "hi"}], tools=tools, tool_choice="auto"
    )

    sent = _sent_tools(mock_completion)
    assert [t["function"]["name"] for t in sent] == ["a", "b"]
    assert all("strict" not in t["function"] for t in sent)
    # The schema itself is unchanged, and the caller's tools are not mutated.
    assert sent[0]["function"]["parameters"] == tools[0]["function"]["parameters"]
    assert all(t["function"]["strict"] is True for t in tools)


@pytest.mark.parametrize(
    "model", ["gpt-5.4", "openai/gpt-4.1", "azure/gpt-4.1", "anthropic/claude-sonnet-4-5"]
)
def test_other_providers_keep_strict(mock_completion, model):
    _make_llm(model).completion(
        messages=[{"role": "user", "content": "hi"}],
        tools=[_strict_tool("a")],
        tool_choice="auto",
    )

    assert _sent_tools(mock_completion)[0]["function"]["strict"] is True
