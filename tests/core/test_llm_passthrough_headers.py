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


def _make_llm(args: dict) -> DefaultLLM:
    """Build a DefaultLLM bypassing __init__/check_llm so we control self.args.

    Mirrors __init__/update_custom_args: consume passthrough_headers out of args
    into an instance attr (it is HolmesGPT-internal config, not a litellm kwarg).
    """
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.model = "test-model"
    llm.api_key = None
    llm.api_base = None
    llm.api_version = None
    llm.args = dict(args)
    llm.tracer = None
    llm.name = None
    llm.is_robusta_model = False
    llm.max_context_size = None
    llm.passthrough_headers = llm.args.pop("passthrough_headers", None)
    return llm


@pytest.fixture
def mock_completion():
    with patch("holmes.core.llm.litellm.completion") as mock:
        mock.return_value = _mock_model_response()
        yield mock


class TestPassthroughHeaders:
    """Whitelisted inbound request_context headers reach litellm extra_headers.

    The modelList ``passthrough_headers`` allowlist names inbound HTTP headers
    (e.g. x-cz-platform) that are copied from request_context["headers"] into
    the outgoing litellm call, so an API gateway in front of HolmesGPT can tag
    every LLM request with business dimensions for spend/usage reporting.
    """

    def test_whitelisted_header_is_forwarded(self, mock_completion):
        llm = _make_llm({"passthrough_headers": ["x-cz-platform", "x-cz-agent"]})
        ctx = {"headers": {"x-cz-platform": "katee", "x-cz-agent": "kateegpt"}}
        llm.completion(
            messages=[{"role": "user", "content": "hi"}], request_context=ctx
        )
        headers = mock_completion.call_args.kwargs["extra_headers"]
        assert headers == {"x-cz-platform": "katee", "x-cz-agent": "kateegpt"}

    def test_non_whitelisted_header_is_not_forwarded(self, mock_completion):
        llm = _make_llm({"passthrough_headers": ["x-cz-platform"]})
        ctx = {"headers": {"x-cz-platform": "katee", "x-evil": "nope"}}
        llm.completion(
            messages=[{"role": "user", "content": "hi"}], request_context=ctx
        )
        headers = mock_completion.call_args.kwargs["extra_headers"]
        assert headers == {"x-cz-platform": "katee"}

    def test_header_lookup_is_case_insensitive(self, mock_completion):
        # HTTP/2 lowercases header names; the ASGI server may preserve case.
        llm = _make_llm({"passthrough_headers": ["x-cz-platform"]})
        ctx = {"headers": {"X-Cz-Platform": "katee"}}
        llm.completion(
            messages=[{"role": "user", "content": "hi"}], request_context=ctx
        )
        headers = mock_completion.call_args.kwargs["extra_headers"]
        assert headers == {"x-cz-platform": "katee"}

    def test_per_request_overrides_static_extra_headers(self, mock_completion):
        llm = _make_llm(
            {
                "extra_headers": {"x-cz-platform": "static", "x-cz-agent": "kateegpt"},
                "passthrough_headers": ["x-cz-platform"],
            }
        )
        ctx = {"headers": {"x-cz-platform": "katee"}}
        llm.completion(
            messages=[{"role": "user", "content": "hi"}], request_context=ctx
        )
        headers = mock_completion.call_args.kwargs["extra_headers"]
        # per-request value wins; untouched static key survives
        assert headers == {"x-cz-platform": "katee", "x-cz-agent": "kateegpt"}

    def test_per_request_overrides_static_header_case_insensitively(
        self, mock_completion
    ):
        # A differently-cased static key (X-Cz-Platform) is the SAME logical HTTP
        # header as the allowlisted x-cz-platform — the request value must replace
        # it, not add a second entry that the proxy could read as the static value.
        llm = _make_llm(
            {
                "extra_headers": {"X-Cz-Platform": "static", "x-cz-agent": "kateegpt"},
                "passthrough_headers": ["x-cz-platform"],
            }
        )
        ctx = {"headers": {"x-cz-platform": "katee"}}
        llm.completion(
            messages=[{"role": "user", "content": "hi"}], request_context=ctx
        )
        headers = mock_completion.call_args.kwargs["extra_headers"]
        # Exactly one logical platform header, carrying the per-request value.
        platform_keys = [k for k in headers if k.lower() == "x-cz-platform"]
        assert platform_keys == ["x-cz-platform"]
        assert headers["x-cz-platform"] == "katee"
        assert headers["x-cz-agent"] == "kateegpt"

    def test_no_request_context_keeps_static_extra_headers(self, mock_completion):
        llm = _make_llm(
            {
                "extra_headers": {"x-cz-platform": "static"},
                "passthrough_headers": ["x-cz-platform"],
            }
        )
        llm.completion(messages=[{"role": "user", "content": "hi"}])
        headers = mock_completion.call_args.kwargs["extra_headers"]
        assert headers == {"x-cz-platform": "static"}

    def test_no_passthrough_config_no_merge(self, mock_completion):
        llm = _make_llm({"extra_headers": {"x-cz-platform": "static"}})
        ctx = {"headers": {"x-cz-platform": "katee"}}
        llm.completion(
            messages=[{"role": "user", "content": "hi"}], request_context=ctx
        )
        headers = mock_completion.call_args.kwargs["extra_headers"]
        # passthrough_headers absent -> request_context ignored entirely
        assert headers == {"x-cz-platform": "static"}

    def test_passthrough_headers_kwarg_not_sent_to_litellm(self, mock_completion):
        # The allowlist is HolmesGPT-internal config, not a litellm kwarg.
        llm = _make_llm({"passthrough_headers": ["x-cz-platform"]})
        ctx = {"headers": {"x-cz-platform": "katee"}}
        llm.completion(
            messages=[{"role": "user", "content": "hi"}], request_context=ctx
        )
        assert "passthrough_headers" not in mock_completion.call_args.kwargs

    def test_self_args_not_mutated_across_requests(self, mock_completion):
        # A shared DefaultLLM serves many requests; merging must not leak one
        # request's headers into self.args (and thus into the next request).
        llm = _make_llm({"passthrough_headers": ["x-cz-platform", "x-cz-team"]})
        ctx1 = {"headers": {"x-cz-platform": "katee", "x-cz-team": "navigator"}}
        llm.completion(
            messages=[{"role": "user", "content": "hi"}], request_context=ctx1
        )

        # Second request carries only one of the headers.
        ctx2 = {"headers": {"x-cz-platform": "katee"}}
        llm.completion(
            messages=[{"role": "user", "content": "hi"}], request_context=ctx2
        )
        headers2 = mock_completion.call_args.kwargs["extra_headers"]
        assert headers2 == {"x-cz-platform": "katee"}  # no x-cz-team leak
        # The shared instance's args must not retain request-scoped headers, and
        # the allowlist lives on the instance (not in args) so it persists across
        # requests without leaking into litellm as a kwarg.
        assert "extra_headers" not in llm.args
        assert "passthrough_headers" not in llm.args
        assert llm.passthrough_headers == ["x-cz-platform", "x-cz-team"]
