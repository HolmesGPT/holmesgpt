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
    llm.model = "openai/Claude Sonnet 4.6"
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


class TestCompletionAttribution:
    """`user` is the standard, provider-neutral end-user identifier; `metadata`
    carries optional observability fields. completion() must forward both so the
    configured observability backend can attribute traces to the end user."""

    def test_user_is_forwarded(self, mock_completion):
        llm = _make_llm()
        llm.completion(
            messages=[{"role": "user", "content": "hi"}], user="alice@example.com"
        )
        assert mock_completion.call_args.kwargs.get("user") == "alice@example.com"

    def test_metadata_is_forwarded(self, mock_completion):
        llm = _make_llm()
        md = {"session_id": "conv-1", "tags": ["request_type:user_chat"]}
        llm.completion(messages=[{"role": "user", "content": "hi"}], metadata=md)
        assert mock_completion.call_args.kwargs.get("metadata") == md

    def test_no_attribution_means_no_kwargs(self, mock_completion):
        """Default behaviour is unchanged: neither kwarg is sent."""
        llm = _make_llm()
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert "user" not in kwargs
        assert "metadata" not in kwargs

    def test_none_attribution_means_no_kwargs(self, mock_completion):
        llm = _make_llm()
        llm.completion(
            messages=[{"role": "user", "content": "hi"}], user=None, metadata=None
        )
        kwargs = mock_completion.call_args.kwargs
        assert "user" not in kwargs
        assert "metadata" not in kwargs

    def test_per_call_user_overrides_configured(self, mock_completion):
        llm = _make_llm({"user": "static-user"})
        llm.completion(messages=[{"role": "user", "content": "hi"}], user="alice")
        assert mock_completion.call_args.kwargs.get("user") == "alice"

    def test_per_call_metadata_merges_over_configured(self, mock_completion):
        """Statically-configured metadata is merged with per-call metadata,
        per-call keys winning on conflict."""
        llm = _make_llm({"metadata": {"session_id": "static", "fixed": "keep"}})
        llm.completion(
            messages=[{"role": "user", "content": "hi"}],
            metadata={"session_id": "s-1", "tags": ["t"]},
        )
        assert mock_completion.call_args.kwargs.get("metadata") == {
            "session_id": "s-1",
            "fixed": "keep",
            "tags": ["t"],
        }

    def test_configured_values_are_not_passed_twice(self, mock_completion):
        """Configured user/metadata must be excluded from the **self.args spread
        so they are not also passed as duplicate kwargs (which would raise)."""
        llm = _make_llm({"user": "u", "metadata": {"fixed": "keep"}})
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs.get("user") == "u"
        assert kwargs.get("metadata") == {"fixed": "keep"}

    def test_configured_values_survive_repeated_calls(self, mock_completion):
        """A reused DefaultLLM must keep its configured user/metadata across
        calls — self.args is read non-destructively, not popped."""
        llm = _make_llm({"user": "u", "metadata": {"fixed": "keep"}})
        for _ in range(2):
            llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs  # last (2nd) call
        assert kwargs.get("user") == "u"
        assert kwargs.get("metadata") == {"fixed": "keep"}
        # completion() does not consume the configured attribution from self.args
        # (it reads them non-destructively, so they persist across calls).
        assert llm.args.get("user") == "u"
        assert llm.args.get("metadata") == {"fixed": "keep"}
