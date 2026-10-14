import litellm
import pytest

from holmes.core.error_info import MAX_REASON_CHARS, ErrorInfo, sanitize_error_text
from holmes.core.relay_refusal import RelayRefusal


@pytest.mark.parametrize(
    "text,secret",
    [
        ("key sk-proj-AAAABBBBCCCCDDDD1234567890 bad", "sk-proj-AAAA"),
        ("key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx_yz-0123 bad", "sk-ant-api03"),
        ("creds " + "AKIA" + "IOSFODNN7EXAMPLE rejected", "IOSFODNN7EXAMPLE"),
        ("token " + "xox" + "b-123456789012-abcdefghijklmnop", "b-123456789012"),
        ("token " + "gh" + "p_abcdefghijklmnopqrstuvwxyz0123456789", "p_abcdefghij"),
        ("Authorization: Bearer abc.def.ghi-JKL_mno=", "abc.def.ghi"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig-nature_x", "eyJhbGci"),
        ("failed with api_key=hunter2&foo=1", "hunter2"),
        ('body {"password": "hunter2", "x": 1}', "hunter2"),
        ("header x-api-key: hunter2 rejected", "hunter2"),
        ("GET https://user:hunter2@host.example.com/v1", "hunter2"),
        ("GET https://host.example.com/v1?sig=hunter2&x=1", "hunter2"),
        ("contact admin@corp.com", "admin@corp.com"),
        ("upstream 10.42.7.19:8443 refused", "10.42.7.19"),
        ("via llm-gw.corp.local down", "llm-gw.corp.local"),
        ("via svc.prod.svc.cluster.local down", "prod.svc.cluster.local"),
        ("via vault.internal down", "vault.internal"),
        ("organization org-9f8e7d6c1234 over quota", "org-9f8e7d6c1234"),
        ("role arn:aws:iam::123456789012:role/prod denied", "123456789012"),
        ("opaque 0123456789abcdef0123456789abcdef0123 value", "0123456789abcdef0123"),
    ],
)
def test_secrets_are_redacted(text, secret):
    assert secret not in sanitize_error_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "Rate limit reached for gpt-4o on tokens per min (TPM): Limit 30000, Used 29000, Requested 5000. Please try again in 2.4s.",
        "Bedrock is unable to process your request: model capacity exhausted in us-east-1",
        "This model's maximum context length is 128000 tokens.",
        "request id req_7f3a91c2 failed",
        "Request ID: 5a1b2c3d-1111-2222-3333-444455556666",
        "see https://platform.openai.com/account/api-keys",
        "model anthropic.claude-3-5-sonnet-20241022-v2:0 not found",
    ],
)
def test_the_providers_own_words_are_kept_verbatim(text):
    assert sanitize_error_text(text) == text


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "litellm.RateLimitError: RateLimitError: OpenAIException - slow down",
            "slow down",
        ),
        (
            "litellm.BadRequestError: ContextWindowExceededError: OpenAIException - too long",
            "too long",
        ),
        (
            'litellm.ServiceUnavailableError: BedrockException - {"message":"Bedrock is unable to process your request."}',
            "Bedrock is unable to process your request.",
        ),
        (
            'litellm.InternalServerError: AnthropicException - {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
            "Overloaded",
        ),
        (
            "litellm.APIError: OpenAIException - Error code: 503 - upstream gone",
            "upstream gone",
        ),
        ("plain failure", "plain failure"),
    ],
)
def test_the_litellm_wrapping_is_stripped(raw, expected):
    assert sanitize_error_text(raw) == expected


def test_html_bodies_are_reduced_to_their_text():
    out = sanitize_error_text("<html><body><h1>502 Bad Gateway</h1>nginx</body></html>")
    assert out == "502 Bad Gateway nginx"


def test_output_is_one_bounded_line():
    out = sanitize_error_text("line1\n\n  line2\t\x00\x1b[31mred\r\n" + "x " * 5000)
    assert "\n" not in out and "\x00" not in out and "\x1b" not in out
    assert len(out) <= MAX_REASON_CHARS
    assert out.endswith("…")


@pytest.mark.parametrize("value", [None, "", "   ", 123, b"bytes", object()])
def test_non_text_input_is_safe(value):
    assert isinstance(sanitize_error_text(value), str)


def test_redaction_cannot_be_split_across_the_truncation_boundary():
    key = "sk-proj-" + "A" * 40
    out = sanitize_error_text("y" * (MAX_REASON_CHARS - 10) + " " + key)
    assert "sk-proj" not in out and "AAAA" not in out


def _litellm(cls, message="OpenAIException - boom", provider="openai"):
    return cls(message=message, model="m", llm_provider=provider)


def test_from_litellm_exception():
    info = ErrorInfo.from_exception(_litellm(litellm.ServiceUnavailableError))

    assert info == ErrorInfo(
        error_class="ServiceUnavailableError",
        status_code=503,
        provider="openai",
        reason="boom",
    )


def test_status_and_provider_are_found_on_the_cause_chain():
    try:
        try:
            raise _litellm(litellm.RateLimitError)
        except litellm.RateLimitError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        info = ErrorInfo.from_exception(outer)

    assert info.error_class == "RuntimeError"
    assert info.status_code == 429
    assert info.provider == "openai"


def test_relay_refusal():
    info = ErrorInfo.from_exception(RelayRefusal("Not allowed for key sk-abcdefghijkl", 403))

    assert (info.error_class, info.status_code) == ("RelayRefusal", 403)
    assert "sk-abcdefghijkl" not in info.reason


def test_bedrock_throttle_raised_as_a_plain_exception_is_classified():
    info = ErrorInfo.from_exception(Exception("Model is getting throttled"))

    assert info.error_class == "RateLimitError"
    assert info.status_code is None


@pytest.mark.parametrize("status", [0, 99, 600, -1, "503", None, True])
def test_a_nonsensical_status_is_dropped(status):
    exc = RuntimeError("x")
    exc.status_code = status  # type: ignore[attr-defined]

    assert ErrorInfo.from_exception(exc).status_code is None


@pytest.mark.parametrize("provider", ["Evil Provider!", "a" * 200, "", None, 5])
def test_a_malformed_provider_is_dropped(provider):
    exc = RuntimeError("x")
    exc.llm_provider = provider  # type: ignore[attr-defined]

    assert ErrorInfo.from_exception(exc).provider is None


def test_a_custom_exception_class_name_is_bounded_and_clean():
    cls = type("Weird" + "N" * 300 + "\nName", (Exception,), {})

    name = ErrorInfo.from_exception(cls("x")).error_class

    assert "\n" not in name and len(name) <= 80


def test_exception_message_that_explodes_in_str_is_survived():
    class Bad(Exception):
        def __str__(self):
            raise ValueError("no str")

    info = ErrorInfo.from_exception(Bad())

    assert info.error_class == "Bad" and info.reason == ""


def test_event_fields_omit_what_is_unknown():
    assert ErrorInfo("RuntimeError", None, None, "x").event_fields() == {
        "error_class": "RuntimeError"
    }
    assert ErrorInfo("RateLimitError", 429, "openai", "x").event_fields() == {
        "error_class": "RateLimitError",
        "provider_status": 429,
        "provider": "openai",
    }


def test_meta_fields_match_the_usage_events_columns_contract():
    assert ErrorInfo("RateLimitError", 429, "openai", "x").meta_fields() == {
        "error_class": "RateLimitError",
        "error_status_code": 429,
        "error_provider": "openai",
    }
    assert ErrorInfo("RuntimeError", None, None, "x").meta_fields() == {
        "error_class": "RuntimeError"
    }


def test_litellm_exceptions_without_an_error_suffix_are_unwrapped():
    exc = litellm.Timeout(message="OpenAIException - took too long", model="m", llm_provider="openai")

    assert ErrorInfo.from_exception(exc).reason == "took too long"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("{not valid json", "{not valid json"),
        ('{"status": 503}', '{"status": 503}'),
        ('{"error": {"code": 7}, "message": "outer wins"}', "outer wins"),
        ("{'msg': 'python repr body', 'error_code': 5405}", "python repr body"),
        ('{"message": "   "}', '{"message": " "}'),
        ("[1, 2]", "[1, 2]"),
    ],
)
def test_error_bodies_unwrap_only_when_they_carry_a_message(raw, expected):
    assert sanitize_error_text(raw) == expected


def test_a_value_whose_str_raises_is_survived():
    class Bad:
        def __str__(self):
            raise ValueError("no str")

    assert sanitize_error_text(Bad()) == ""


def test_the_cause_chain_may_loop():
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__cause__, b.__cause__ = b, a
    b.status_code = 502  # type: ignore[attr-defined]

    assert ErrorInfo.from_exception(a).status_code == 502


def test_from_provider_distinguishes_provider_errors_from_internal_ones():
    assert ErrorInfo.from_exception(_litellm(litellm.RateLimitError)).from_provider
    assert not ErrorInfo.from_exception(RuntimeError("tool bug")).from_provider
