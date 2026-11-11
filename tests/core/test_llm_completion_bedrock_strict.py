import copy
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
                "properties": {
                    "q": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "opts": {
                        "type": "object",
                        "properties": {"n": {"type": "integer"}},
                        "required": ["n"],
                        "additionalProperties": False,
                    },
                },
                "required": ["q", "opts"],
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
        # Bare Bedrock ids: litellm routes these to Bedrock without a prefix.
        ("us.anthropic.claude-opus-4-6-v1", {}),
        ("global.anthropic.claude-sonnet-4-5-20250929-v1:0", {}),
    ],
)
def test_bedrock_tools_are_sent_without_strict_signals(mock_completion, model, args):
    tools = [_strict_tool("a"), _strict_tool("b")]
    original = copy.deepcopy(tools)
    _make_llm(model, args).completion(
        messages=[{"role": "user", "content": "hi"}], tools=tools, tool_choice="auto"
    )

    sent = _sent_tools(mock_completion)
    assert [t["function"]["name"] for t in sent] == ["a", "b"]
    for tool in sent:
        assert "strict" not in tool["function"]
        # litellm copies the top-level additionalProperties into the Bedrock toolSpec,
        # which still makes Bedrock compile the schema as strict.
        assert "additionalProperties" not in tool["function"]["parameters"]
    # The rest of the schema is unchanged, nested additionalProperties included.
    expected = copy.deepcopy(original[0]["function"]["parameters"])
    del expected["additionalProperties"]
    assert sent[0]["function"]["parameters"] == expected
    # The caller's tools are not mutated.
    assert tools == original


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.4",
        "openai/gpt-4.1",
        "azure/gpt-4.1",
        "anthropic/claude-sonnet-4-5",
        # Robusta AI routes are sent as openai/<id>; relay handles Bedrock itself.
        "openai/eu.anthropic.claude-opus-4-6-v1",
    ],
)
def test_other_providers_keep_strict(mock_completion, model):
    tool = _strict_tool("a")
    _make_llm(model).completion(
        messages=[{"role": "user", "content": "hi"}],
        tools=[tool],
        tool_choice="auto",
    )

    sent = _sent_tools(mock_completion)[0]["function"]
    assert sent["strict"] is True
    assert sent["parameters"] == tool["function"]["parameters"]


def test_unknown_model_keeps_strict(mock_completion):
    """A model litellm cannot map to a provider is not treated as Bedrock."""
    _make_llm("some-unknown-model-xyz").completion(
        messages=[{"role": "user", "content": "hi"}],
        tools=[_strict_tool("a")],
        tool_choice="auto",
    )

    assert _sent_tools(mock_completion)[0]["function"]["strict"] is True
