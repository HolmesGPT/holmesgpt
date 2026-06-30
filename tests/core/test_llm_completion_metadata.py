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


def _make_llm(args: dict | None = None) -> DefaultLLM:
    """Build a DefaultLLM bypassing __init__/check_llm so we can control self.args."""
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.model = "bedrock/anthropic.claude-sonnet-4-20250514-v1:0"
    llm.api_key = None
    llm.api_base = None
    llm.api_version = None
    llm.args = args or {}
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


class TestCompletionMetadata:
    """`metadata` is LiteLLM's reserved, callback-agnostic observability field.
    completion() must forward it so the active logging backend / LiteLLM proxy
    (Langfuse, Langsmith, Arize, ...) can attribute traces to the end user."""

    def test_metadata_is_forwarded(self, mock_completion):
        llm = _make_llm()
        md = {"trace_user_id": "alice@example.com", "session_id": "conv-1"}
        llm.completion(messages=[{"role": "user", "content": "hi"}], metadata=md)
        assert mock_completion.call_args.kwargs.get("metadata") == md

    def test_no_metadata_means_no_kwarg(self, mock_completion):
        """Default behaviour is unchanged: no metadata kwarg is sent at all."""
        llm = _make_llm()
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        assert "metadata" not in mock_completion.call_args.kwargs

    def test_none_metadata_means_no_kwarg(self, mock_completion):
        llm = _make_llm()
        llm.completion(messages=[{"role": "user", "content": "hi"}], metadata=None)
        assert "metadata" not in mock_completion.call_args.kwargs

    def test_per_call_metadata_merges_over_configured(self, mock_completion):
        """Statically-configured metadata is merged with per-call metadata,
        per-call keys winning on conflict."""
        llm = _make_llm({"metadata": {"trace_user_id": "static", "fixed": "keep"}})
        llm.completion(
            messages=[{"role": "user", "content": "hi"}],
            metadata={"trace_user_id": "alice", "session_id": "s-1"},
        )
        assert mock_completion.call_args.kwargs.get("metadata") == {
            "trace_user_id": "alice",
            "fixed": "keep",
            "session_id": "s-1",
        }

    def test_metadata_is_not_passed_twice(self, mock_completion):
        """Configured metadata must be popped from self.args so it is not also
        spread as a duplicate kwarg (which would raise TypeError)."""
        llm = _make_llm({"metadata": {"fixed": "keep"}})
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        assert mock_completion.call_args.kwargs.get("metadata") == {"fixed": "keep"}
