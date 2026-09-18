from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.core.llm import DefaultLLM


def _tool(index: int) -> dict:
    return {
        "type": "function",
        "function": {
            "name": f"tool_{index}",
            "description": "Synthetic tool used to verify the request boundary.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _response() -> ModelResponse:
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


def test_configured_tool_limit_rejects_request_before_litellm_call():
    with patch.object(DefaultLLM, "check_llm"):
        llm = DefaultLLM(
            model="openai/gpt-4.1",
            args={"custom_args": {"max_tools": 128}},
        )

    with patch("holmes.core.llm.litellm.completion") as completion:
        with pytest.raises(ValueError) as exc_info:
            llm.completion(
                messages=[{"role": "user", "content": "Investigate the incident"}],
                tools=[_tool(index) for index in range(129)],
                tool_choice="auto",
            )

    message = str(exc_info.value)
    assert "openai/gpt-4.1" in message
    assert "129" in message
    assert "128" in message
    completion.assert_not_called()


def test_configured_tool_limit_must_be_a_positive_integer():
    with patch.object(DefaultLLM, "check_llm"):
        with pytest.raises(ValueError, match="max_tools must be a positive integer"):
            DefaultLLM(
                model="openai/gpt-4.1",
                args={"custom_args": {"max_tools": "128"}},
            )


@pytest.mark.parametrize("tool_count", [127, 128])
def test_request_at_or_below_configured_tool_limit_is_sent(tool_count: int):
    with patch.object(DefaultLLM, "check_llm"):
        llm = DefaultLLM(
            model="openai/gpt-4.1",
            args={"custom_args": {"max_tools": 128}},
        )

    tools = [_tool(index) for index in range(tool_count)]
    with patch("holmes.core.llm.litellm.completion", return_value=_response()) as completion:
        llm.completion(
            messages=[{"role": "user", "content": "Investigate the incident"}],
            tools=tools,
            tool_choice="auto",
        )

    assert completion.call_args.kwargs["tools"] == tools
    assert "max_tools" not in completion.call_args.kwargs


def test_request_without_configured_tool_limit_preserves_existing_behavior():
    with patch.object(DefaultLLM, "check_llm"):
        llm = DefaultLLM(model="custom/provider-model")

    tools = [_tool(index) for index in range(129)]
    with patch("holmes.core.llm.litellm.completion", return_value=_response()) as completion:
        llm.completion(
            messages=[{"role": "user", "content": "Investigate the incident"}],
            tools=tools,
            tool_choice="auto",
        )

    assert completion.call_args.kwargs["tools"] == tools
