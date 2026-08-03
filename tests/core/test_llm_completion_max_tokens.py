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


def _make_llm(
    args: dict,
    model: str = "test-model",
    max_context_size=None,
    max_output_tokens=None,
) -> DefaultLLM:
    """Build a DefaultLLM bypassing __init__/check_llm so we can control self.args directly."""
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.model = model
    llm.api_key = None
    llm.api_base = None
    llm.api_version = None
    llm.args = dict(args)
    llm.tracer = None
    llm.name = None
    llm.is_robusta_model = False
    llm.max_context_size = max_context_size
    llm.max_output_tokens = max_output_tokens
    return llm


@pytest.fixture
def mock_completion():
    with patch("holmes.core.llm.litellm.completion") as mock:
        mock.return_value = _mock_model_response()
        yield mock


class TestCompletionMaxTokensHandling:
    """Verify an explicit output-token limit is always forwarded to litellm.completion.

    Without an explicit max_tokens, litellm falls back to provider defaults —
    4096 for Anthropic models missing from its cost map (e.g. proxy aliases) —
    silently truncating long answers with finish_reason="length", while Holmes
    budgets input space for get_maximum_output_token() that is never enforced.

    Behavior matrix:
      1. args={}                                  -> inject get_maximum_output_token()
      2. args={max_tokens: 8000}                  -> forward 8000 (user wins)
      3. args={max_tokens: None}                  -> strip null sentinel, inject computed
      4. args={max_completion_tokens: 8000}       -> forward as-is, do NOT inject max_tokens
      5. args={max_completion_tokens: None}       -> strip null sentinel, inject computed
      6. OVERRIDE_MAX_OUTPUT_TOKEN set            -> injected value honors the override
      7. known model in litellm cost map          -> injected value is the model's max
      8. unknown model                            -> injected value is the fallback,
                                                     never extrapolated from the window
    """

    def test_unknown_model_with_large_context_is_not_extrapolated(
        self, mock_completion
    ):
        """A large context window is not evidence of a large output ceiling. An
        unknown model gets the fallback limit whatever its window size, because
        asking for more than the model allows does not produce a longer answer —
        the request is capped at the provider's own default instead."""
        llm = _make_llm({}, model="proxy/some-claude-alias", max_context_size=1_000_000)
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["max_tokens"] == 64000

    def test_small_context_unknown_model_gets_the_same_fallback(self, mock_completion):
        """The fallback does not scale with the window in either direction."""
        llm = _make_llm({}, model="proxy/some-claude-alias", max_context_size=200_000)
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["max_tokens"] == 64000

    def test_unknown_model_no_args_no_context_uses_fallback_not_4096(
        self, mock_completion
    ):
        """A model litellm doesn't know, with no max_tokens and no context
        override, must send the fallback limit — NOT litellm's silent 4096
        Anthropic default."""
        llm = _make_llm({}, model="proxy/unknown-claude", max_context_size=None)
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["max_tokens"] == 64000
        assert kwargs["max_tokens"] != 4096

    def test_injected_limit_matches_get_maximum_output_token(self, mock_completion):
        """Row 1: the enforced limit is exactly the budget input limiting reserves."""
        llm = _make_llm({})
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["max_tokens"] == llm.get_maximum_output_token()

    def test_user_max_tokens_wins(self, mock_completion):
        """Row 2: an explicit max_tokens in model args is forwarded unchanged."""
        llm = _make_llm({"max_tokens": 8000})
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["max_tokens"] == 8000

    def test_config_null_max_tokens_is_replaced(self, mock_completion):
        """Row 3: modelList `max_tokens: null` must not leak as None nor block injection."""
        llm = _make_llm({"max_tokens": None})
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["max_tokens"] == llm.get_maximum_output_token()

    def test_user_max_completion_tokens_blocks_injection(self, mock_completion):
        """Row 4: a user-set max_completion_tokens must not be joined by a
        conflicting injected max_tokens."""
        llm = _make_llm({"max_completion_tokens": 8000})
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["max_completion_tokens"] == 8000
        assert "max_tokens" not in kwargs

    def test_config_null_max_completion_tokens_is_stripped(self, mock_completion):
        """Row 5: modelList `max_completion_tokens: null` is stripped and the
        computed max_tokens is injected instead."""
        llm = _make_llm({"max_completion_tokens": None})
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert "max_completion_tokens" not in kwargs
        assert kwargs["max_tokens"] == llm.get_maximum_output_token()

    def test_override_env_var_reaches_the_request(self, mock_completion):
        """Row 6: OVERRIDE_MAX_OUTPUT_TOKEN is the documented escape hatch and
        must now flow through to the actual request."""
        with patch("holmes.core.llm.OVERRIDE_MAX_OUTPUT_TOKEN", 12345):
            llm = _make_llm({})
            llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["max_tokens"] == 12345

    def test_known_model_capped_at_litellm_model_max(self, mock_completion):
        """Row 7: for models litellm knows, the injected limit never exceeds the
        model's real max_output_tokens (gpt-4o caps at 16384, below the 64k floor)."""
        llm = _make_llm({}, model="gpt-4o")
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["max_tokens"] == llm.get_maximum_output_token()
        assert kwargs["max_tokens"] <= 16384


class TestDeclaredMaxOutputTokens:
    """A model's output ceiling can be declared alongside its context window
    (`custom_args.max_output_tokens`). Without it, an unknown model falls back to
    a fixed assumption, and the value Holmes reports must be the value it sends —
    otherwise clients are shown a limit nobody enforces.

    Resolution order asserted here:
      1. max_tokens / max_completion_tokens in the model args
      2. OVERRIDE_MAX_OUTPUT_TOKEN
      3. declared max_output_tokens (capped by litellm's value when known)
      4. litellm's value for the model
      5. the fallback
    """

    def test_declared_limit_is_reported_and_sent(self, mock_completion):
        llm = _make_llm(
            {},
            model="proxy/some-claude-alias",
            max_context_size=1_000_000,
            max_output_tokens=32_000,
        )
        assert llm.get_maximum_output_token() == 32_000
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        assert mock_completion.call_args.kwargs["max_tokens"] == 32_000

    def test_reported_limit_matches_args_limit(self, mock_completion):
        """An args limit is what completion() enforces, so it must also be what
        get_maximum_output_token() reports — that value is reserved from the input
        budget and reported to clients."""
        llm = _make_llm(
            {"max_tokens": 8000},
            model="proxy/some-claude-alias",
            max_context_size=1_000_000,
            max_output_tokens=32_000,
        )
        assert llm.get_maximum_output_token() == 8000
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        assert mock_completion.call_args.kwargs["max_tokens"] == 8000

    def test_reported_limit_matches_args_completion_tokens_limit(self):
        """Same for max_completion_tokens, which completion() forwards as-is."""
        llm = _make_llm({"max_completion_tokens": 5000}, max_context_size=1_000_000)
        assert llm.get_maximum_output_token() == 5000

    def test_override_env_var_beats_declared_limit(self):
        with patch("holmes.core.llm.OVERRIDE_MAX_OUTPUT_TOKEN", 12345):
            llm = _make_llm({}, max_context_size=1_000_000, max_output_tokens=32_000)
            assert llm.get_maximum_output_token() == 12345

    def test_declared_limit_capped_at_litellm_model_max(self):
        """A declaration above what the model can actually produce is clamped down
        for models litellm knows (gpt-4o caps at 16384)."""
        llm = _make_llm({}, model="gpt-4o", max_output_tokens=999_999)
        assert llm.get_maximum_output_token() <= 16384

    def test_instance_without_declared_limit_falls_back(self):
        """max_output_tokens is a class-level default, so instances that never ran
        update_custom_args() keep working instead of raising."""
        llm = DefaultLLM.__new__(DefaultLLM)
        llm.model = "proxy/some-claude-alias"
        llm.args = {}
        assert llm.get_maximum_output_token() == 64000


class TestUpdateCustomArgs:
    """custom_args carries the limits declared for the model and must not leak
    into the completion call."""

    def test_extracts_both_limits_and_pops_custom_args(self):
        llm = _make_llm(
            {"custom_args": {"max_context_size": 400_000, "max_output_tokens": 32_000}}
        )
        llm.update_custom_args()
        assert llm.max_context_size == 400_000
        assert llm.max_output_tokens == 32_000
        assert "custom_args" not in llm.args

    def test_max_input_tokens_is_accepted_as_the_context_window(self):
        """max_input_tokens is litellm's name for the context window."""
        llm = _make_llm({"custom_args": {"max_input_tokens": 400_000}})
        llm.update_custom_args()
        assert llm.get_context_window_size() == 400_000
        assert llm.get_maximum_output_token() == 64000

    def test_missing_custom_args_leaves_limits_unset(self):
        llm = _make_llm({})
        llm.update_custom_args()
        assert llm.max_context_size is None
        assert llm.max_output_tokens is None
