from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.core.llm import ROBUSTA_CONVERSATION_ID_HEADER, DefaultLLM


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


def _robusta_llm(args: dict, conversation_id=None) -> DefaultLLM:
    # check_llm returns early for Robusta models, so no provider env is needed.
    return DefaultLLM(
        model="azure/gpt-5-mini",
        api_key="account token",
        api_base="https://api.robusta.dev/llm/Robusta",
        args=dict(args),
        is_robusta_model=True,
        conversation_id=conversation_id,
    )


@pytest.fixture
def mock_completion():
    with patch("holmes.core.llm.litellm.completion") as mock:
        mock.return_value = _mock_model_response()
        yield mock


class TestConversationIdHeader:
    def test_robusta_model_sends_conversation_id(self, mock_completion):
        llm = _robusta_llm({}, conversation_id="conv-1")
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        headers = mock_completion.call_args.kwargs["extra_headers"]
        assert headers == {ROBUSTA_CONVERSATION_ID_HEADER: "conv-1"}

    def test_every_completion_sends_it(self, mock_completion):
        llm = _robusta_llm({}, conversation_id="conv-1")
        for _ in range(2):
            llm.completion(messages=[{"role": "user", "content": "hi"}])
            headers = mock_completion.call_args.kwargs["extra_headers"]
            assert headers[ROBUSTA_CONVERSATION_ID_HEADER] == "conv-1"

    def test_keeps_model_extra_headers(self, mock_completion):
        llm = _robusta_llm({"extra_headers": {"X-Model": "1"}}, conversation_id="c")
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        headers = mock_completion.call_args.kwargs["extra_headers"]
        assert headers == {"X-Model": "1", ROBUSTA_CONVERSATION_ID_HEADER: "c"}

    def test_keeps_extra_headers_env(self, mock_completion):
        with patch("holmes.core.llm.EXTRA_HEADERS", '{"X-Env": "1"}'):
            llm = _robusta_llm({}, conversation_id="c")
            llm.completion(messages=[{"role": "user", "content": "hi"}])
        headers = mock_completion.call_args.kwargs["extra_headers"]
        assert headers == {"X-Env": "1", ROBUSTA_CONVERSATION_ID_HEADER: "c"}

    def test_no_conversation_id_sends_no_header(self, mock_completion):
        llm = _robusta_llm({})
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        assert "extra_headers" not in mock_completion.call_args.kwargs

    def test_customer_model_never_keeps_it(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        llm = DefaultLLM(model="gpt-4o", conversation_id="conv-1")
        assert llm.conversation_id is None
