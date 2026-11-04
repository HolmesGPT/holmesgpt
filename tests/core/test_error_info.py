import time

import httpx
import litellm
import pytest

from holmes.core.error_info import (
    MAX_MESSAGE_CHARS,
    ErrorInfo,
    parse_provider_text,
    short_message,
)
from holmes.core.relay_refusal import RelayRefusal


@pytest.mark.parametrize(
    "text",
    [
        "Bedrock is unable to process your request.",
        "Overloaded",
        "Connection error.",
        "The model is overloaded. Please retry.",
        "Too many requests, please wait before trying again.",
        "Your Robusta AI account limit has been reached.",
        "The server doesn't support this request.",
    ],
)
def test_plain_prose_is_kept_verbatim(text):
    assert short_message(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "Incorrect API key provided: sk-proj-AAAABBBBCCCCDDDD1234567890.",
        "Rate limit reached for gpt-4o in organization org-XYZ on tokens per min (TPM): Limit 30000.",
        "model capacity exhausted in us-east-1 (request id req_7f3a91c2)",
        "upstream 10.1.2.3:8443 refused",
        "failed for admin@corp.com",
        "see https://platform.openai.com/account/api-keys",
        "connect to 2001:db8::1234: refused",
        "via llm-gw.corp.local down",
        "path /app/site-packages/litellm/main.py failed",
        "token AbCdEfGhIjKlMnOpQrStUvWxYz failed",
        "key=hunter2",
        "password hunter2 rejected 42",
        "x" * 30,
        "<html><body>502 Bad Gateway</body></html>",
        "a." * 100,
        "",
        "   ",
    ],
)
def test_anything_that_is_not_plain_prose_is_dropped_not_scrubbed(text):
    assert short_message(text) is None


def test_only_the_leading_prose_sentences_are_kept():
    assert (
        short_message("Your Robusta AI account limit has been reached. Contact support@robusta.dev to increase limits.")
        == "Your Robusta AI account limit has been reached."
    )
    assert short_message("First one here. Second one here. Third one here.") == "First one here. Second one here."
    assert short_message("Fine sentence. Bad sentence 42. Fine again.") == "Fine sentence."


def test_only_the_first_line_is_read_so_a_traceback_is_never_seen():
    text = (
        "Connection error.\nTraceback (most recent call last):\n"
        '  File "/app/main.py", line 5, in completion\nValueError: boom'
    )

    assert short_message(text) == "Connection error."
    assert short_message("Traceback (most recent call last):\n  Connection error.") is None


def test_a_message_over_the_limit_is_dropped():
    sentence = "word " * 30
    assert short_message(sentence.strip() + ".") is not None
    assert short_message((sentence * 10).strip() + ".") is None
    assert MAX_MESSAGE_CHARS == 200


@pytest.mark.parametrize("value", [None, 5, b"bytes", ["a"], object()])
def test_non_text_is_not_a_message(value):
    assert short_message(value) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            'litellm.ServiceUnavailableError: BedrockException - {"message":"Bedrock is unable to process your request."}',
            (None, "Bedrock is unable to process your request."),
        ),
        (
            'litellm.InternalServerError: AnthropicException - {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
            ("overloaded_error", "Overloaded"),
        ),
        (
            'litellm.RateLimitError: RateLimitError: OpenAIException - {"error": {"message": "Rate limit reached for gpt-4o in org-X: Limit 3", "type": "tokens", "code": "rate_limit_exceeded"}}',
            ("rate_limit_exceeded", None),
        ),
        (
            "litellm.AuthenticationError: AuthenticationError: OpenAIException - Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-proj-AAAA1234.', 'type': 'invalid_request_error', 'code': 'invalid_api_key'}}",
            ("invalid_api_key", None),
        ),
        (
            "litellm.APIError: OpenAIException - Error code: 429 - {'error': {'message': 'Your Robusta AI account limit has been reached. Contact support@robusta.dev to increase limits.', 'type': 'rate_limit_exceeded', 'code': 'account_limit_reached', 'robusta_error_code': 4829}}",
            ("account_limit_reached", "Your Robusta AI account limit has been reached."),
        ),
        (
            "litellm.BadGatewayError: OpenAIException - Error code: 502 - {'msg': 'An error occurred while communicating with the AI service.', 'error_code': 5406, 'provider_code': 'bad_gateway', 'provider_message': 'Upstream failed.'}",
            ("bad_gateway", "Upstream failed."),
        ),
        (
            '{"__type": "com.amazon.coral.availability#ThrottlingException", "message": "Too many requests"}',
            ("ThrottlingException", "Too many requests"),
        ),
        ("litellm.Timeout: OpenAIException - Request timed out.", (None, "Request timed out.")),
        ("litellm.APIConnectionError: OpenAIException - <html>502 Bad Gateway</html>", (None, None)),
        ("{not valid json", (None, None)),
        ('{"status": 503}', (None, None)),
        ('{"error": {"code": "has spaces and 12345678901"}}', (None, None)),
        ('{"error": {"code": "a1b2c3d4e5f6g7h8i9"}}', (None, None)),
        ('{"error": {"code": 42, "message": 7}}', (None, None)),
    ],
)
def test_the_provider_body_is_read_field_by_field(raw, expected):
    assert parse_provider_text(raw) == expected


def test_a_huge_body_is_not_parsed():
    body = '{"message": "Overloaded", "pad": "' + "x" * 70_000 + '"}'

    assert parse_provider_text(body) == (None, None)


def _litellm(cls, message="OpenAIException - boom.", provider="openai"):
    return cls(message=message, model="m", llm_provider=provider)


def test_from_litellm_exception():
    info = ErrorInfo.from_exception(
        _litellm(litellm.ServiceUnavailableError, 'BedrockException - {"message":"Model overloaded."}', "bedrock")
    )

    assert info == ErrorInfo(
        error_class="ServiceUnavailableError",
        status_code=503,
        provider="bedrock",
        code=None,
        message="Model overloaded.",
    )


def test_a_failure_wrapped_in_a_non_llm_exception_is_still_classified():
    try:
        try:
            raise _litellm(litellm.RateLimitError)
        except litellm.RateLimitError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        info = ErrorInfo.from_exception(outer)

    assert info.error_class == "RuntimeError"
    assert (info.status_code, info.provider, info.message) == (429, "openai", "boom.")
    assert info.from_provider


def test_the_cause_chain_may_loop():
    a, b = RuntimeError("a"), _litellm(litellm.BadGatewayError)
    a.__cause__, b.__cause__ = b, a

    assert ErrorInfo.from_exception(a).status_code == 502


def test_relay_refusal():
    info = ErrorInfo.from_exception(RelayRefusal("Robusta AI is disabled for this account.", 403))

    assert (info.error_class, info.status_code) == ("RelayRefusal", 403)
    assert info.message == "Robusta AI is disabled for this account."
    assert info.from_provider


def test_a_relay_refusal_with_identifiers_keeps_no_message():
    info = ErrorInfo.from_exception(RelayRefusal("Not allowed for key sk-abcdefghijkl", 403))

    assert info.message is None and info.status_code == 403


def test_bedrock_throttle_raised_as_a_plain_exception_is_classified():
    info = ErrorInfo.from_exception(Exception("Model is getting throttled"))

    assert info.error_class == "RateLimitError"
    assert (info.status_code, info.provider, info.message) == (None, "bedrock", None)


@pytest.mark.parametrize("status", [0, 99, 600, -1, "503", None, True])
def test_a_nonsensical_status_is_dropped(status):
    exc = _litellm(litellm.InternalServerError)
    exc.status_code = status

    assert ErrorInfo.from_exception(exc).status_code is None


@pytest.mark.parametrize("provider", ["Evil Provider!", "a" * 200, "", None, 5])
def test_a_malformed_provider_is_dropped(provider):
    exc = _litellm(litellm.RateLimitError)
    exc.llm_provider = provider

    assert ErrorInfo.from_exception(exc).provider is None


def test_a_custom_exception_class_name_is_bounded_and_clean():
    cls = type("Weird" + "N" * 300 + "\nName", (Exception,), {})

    name = ErrorInfo.from_exception(cls("x")).error_class

    assert "\n" not in name and len(name) <= 80


def test_an_exception_whose_str_raises_is_survived():
    class Bad(litellm.APIError):
        def __str__(self):
            raise ValueError("no str")

    info = ErrorInfo.from_exception(Bad(status_code=500, message="m", llm_provider="openai", model="m"))

    assert info.status_code == 500 and info.message is None


def test_a_raising_status_property_does_not_break_classification():
    class Boom(litellm.ServiceUnavailableError):
        armed = False

        @property
        def status_code(self):
            if self.armed:
                raise RuntimeError("property blew up")
            return 503

        @status_code.setter
        def status_code(self, value):
            pass

        @property
        def llm_provider(self):
            if self.armed:
                raise RuntimeError("property blew up")
            return "p"

        @llm_provider.setter
        def llm_provider(self, value):
            pass

    exc = Boom(message="x", model="m", llm_provider="p")
    Boom.armed = True

    info = ErrorInfo.from_exception(exc)

    assert info.error_class == "Boom" and info.status_code is None and info.provider is None


def test_a_non_llm_exception_with_a_status_code_is_not_a_provider_error():
    class HttpFrameworkError(Exception):
        status_code = 502

    info = ErrorInfo.from_exception(HttpFrameworkError("db password=hunter2 down"))

    assert not info.from_provider
    assert (info.status_code, info.code, info.message) == (None, None, None)
    assert info.event_fields() == {"error_class": "HttpFrameworkError"}


def test_event_and_meta_fields_omit_what_is_unknown():
    bare = ErrorInfo("RuntimeError", None, None)
    full = ErrorInfo("RateLimitError", 429, "openai", "rate_limit_exceeded", "Slow down.")

    assert bare.event_fields() == {"error_class": "RuntimeError"}
    assert bare.meta_fields() == {"error_class": "RuntimeError"}
    assert full.event_fields() == {
        "error_class": "RateLimitError",
        "provider_status": 429,
        "provider": "openai",
        "provider_code": "rate_limit_exceeded",
    }
    assert full.meta_fields() == {
        "error_class": "RateLimitError",
        "error_status_code": 429,
        "error_provider": "openai",
        "error_provider_code": "rate_limit_exceeded",
    }


def test_the_outermost_llm_error_supplies_the_message():
    outer = _litellm(litellm.RateLimitError, "OpenAIException - Outer failure.")
    outer.__cause__ = _litellm(litellm.BadGatewayError, "OpenAIException - Inner failure.")

    info = ErrorInfo.from_exception(outer)

    assert (info.message, info.status_code) == ("Outer failure.", 429)


@pytest.mark.parametrize(
    "suffix",
    [
        " LiteLLM Retried: 2 times",
        " LiteLLM Retried: 2 times, LiteLLM Max Retries: 3",
        "\nLiteLLM Retried: 1 times, LiteLLM Max Retries: 2\n",
    ],
)
def test_litellm_retry_text_after_the_body_does_not_hide_the_provider_fields(suffix):
    raw = (
        "litellm.ServiceUnavailableError: AnthropicException - "
        '{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}' + suffix
    )

    assert parse_provider_text(raw) == ("overloaded_error", "Overloaded")


def test_litellm_retry_text_after_a_plain_message_is_not_part_of_it():
    assert parse_provider_text("litellm.Timeout: OpenAIException - Request timed out. LiteLLM Retried: 2 times") == (
        None,
        "Request timed out.",
    )


def test_arbitrary_text_after_the_body_is_still_rejected():
    raw = '{"error": {"type": "overloaded_error", "message": "Overloaded"}} and some trailing text'

    assert parse_provider_text(raw) == (None, None)


def test_the_structured_body_on_the_exception_is_read_before_its_text():
    exc = _litellm(litellm.RateLimitError, "OpenAIException - Rate limit reached for gpt-4o in org-X: Limit 3")
    exc.body = {"message": "Rate limit reached.", "type": "tokens", "code": "rate_limit_exceeded"}

    info = ErrorInfo.from_exception(exc)

    assert (info.code, info.message) == ("rate_limit_exceeded", "Rate limit reached.")


def test_the_json_of_the_wrapped_response_is_read_when_there_is_no_body_attribute():
    exc = _litellm(litellm.ServiceUnavailableError, "OpenAIException - gateway text: 10.0.0.1")
    exc.response = httpx.Response(
        503,
        json={"error": {"message": "Overloaded", "type": "overloaded_error"}},
        request=httpx.Request("POST", "http://x"),
    )

    info = ErrorInfo.from_exception(exc)

    assert (info.code, info.message) == ("overloaded_error", "Overloaded")


@pytest.mark.parametrize("body", [None, "text", ["a"], {}, {"error": "flat"}])
def test_a_body_without_usable_fields_falls_back_to_the_text(body):
    exc = _litellm(litellm.ServiceUnavailableError, "OpenAIException - Overloaded.")
    exc.body = body

    assert ErrorInfo.from_exception(exc).message == "Overloaded."


def test_a_response_that_cannot_be_decoded_falls_back_to_the_text():
    exc = _litellm(litellm.ServiceUnavailableError, "OpenAIException - Overloaded.")
    exc.response = httpx.Response(503, content=b"<html>", request=httpx.Request("POST", "http://x"))

    assert ErrorInfo.from_exception(exc).message == "Overloaded."


def test_the_length_limit_is_reached_by_valid_prose():
    long_sentence = " ".join(["overloadedness"] * 12) + "."

    assert short_message(long_sentence) == long_sentence
    assert len(long_sentence) <= MAX_MESSAGE_CHARS
    assert short_message(long_sentence + " " + long_sentence) is None


def test_summary_names_class_status_provider_code_and_message():
    info = ErrorInfo("RateLimitError", 429, "openai", "rate_limit", "Slow down.")

    assert info.summary() == (
        "AI provider error: RateLimitError (429, openai, rate_limit). Slow down."
    )


def test_summary_omits_what_is_unknown():
    assert ErrorInfo("Timeout", None, "x").summary() == "AI provider error: Timeout (x)."
    assert ErrorInfo("Timeout", 408, None).summary() == "AI provider error: Timeout (408)."
    assert ErrorInfo("Timeout", None, None, None, "Late.").summary() == (
        "AI provider error: Timeout. Late."
    )


def test_the_provider_relay_reports_wins_over_the_one_litellm_sees():
    exc = litellm.ServiceUnavailableError(
        message="OpenAIException - Capacity.",
        model="m",
        llm_provider="openai",
        response=None,
    )
    exc.body = {
        "msg": "x",
        "error_class": "ServiceUnavailableError",
        "provider": "bedrock",
        "provider_code": "capacity",
    }

    info = ErrorInfo.from_exception(exc)

    assert info.provider == "bedrock"
    assert info.code == "capacity"


def test_a_provider_name_in_the_body_that_is_not_a_name_is_ignored():
    exc = litellm.ServiceUnavailableError(
        message="OpenAIException - Capacity.", model="m", llm_provider="openai"
    )
    exc.body = {"provider": "http://10.1.2.3/x", "message": "Capacity."}

    assert ErrorInfo.from_exception(exc).provider == "openai"


def test_the_provider_relay_reports_in_the_exception_text_is_used_too():
    exc = litellm.ServiceUnavailableError(
        message="OpenAIException - Error code: 503 - {'msg': 'x', 'error_class': "
        "'ServiceUnavailableError', 'provider': 'bedrock', 'provider_code': 'capacity'}",
        model="m",
        llm_provider="openai",
    )

    info = ErrorInfo.from_exception(exc)

    assert info.provider == "bedrock"
    assert info.code == "capacity"


def test_the_provider_in_the_text_body_is_used_when_the_exception_body_is_unusable():
    exc = litellm.ServiceUnavailableError(
        message="OpenAIException - Error code: 503 - {'msg': 'x', 'error_class': "
        "'ServiceUnavailableError', 'provider': 'bedrock', 'provider_code': 'capacity'}",
        model="m",
        llm_provider="openai",
    )
    exc.body = {"unrelated": "field"}

    info = ErrorInfo.from_exception(exc)

    assert info.provider == "bedrock"
    assert info.code == "capacity"


def test_a_provider_key_in_a_body_that_is_not_relays_is_ignored():
    exc = litellm.ServiceUnavailableError(
        message="OpenAIException - Capacity.", model="m", llm_provider="openai"
    )
    exc.body = {"provider": "bedrock", "message": "Capacity."}

    assert ErrorInfo.from_exception(exc).provider == "openai"


def test_the_generic_anthropic_envelope_type_is_not_a_code():
    exc = litellm.ServiceUnavailableError(
        message="AnthropicException - x", model="m", llm_provider="anthropic"
    )
    exc.body = {"type": "error", "error": {"message": "Overloaded"}}

    info = ErrorInfo.from_exception(exc)

    assert info.code is None
    assert info.message == "Overloaded"


def test_a_specific_nested_type_is_still_the_code():
    exc = litellm.ServiceUnavailableError(
        message="AnthropicException - x", model="m", llm_provider="anthropic"
    )
    exc.body = {
        "type": "error",
        "error": {"type": "overloaded_error", "message": "Overloaded"},
    }

    assert ErrorInfo.from_exception(exc).code == "overloaded_error"


def test_a_long_whitespace_run_in_the_text_is_not_quadratic():
    started = time.monotonic()
    parse_provider_text("OpenAIException - a" + " " * 400_000 + "b")

    assert time.monotonic() - started < 2


def test_only_a_bare_exception_is_a_bedrock_throttle():
    assert ErrorInfo.from_exception(RuntimeError("Model is getting throttled")).error_class == "RuntimeError"
    assert ErrorInfo.from_exception(Exception("Model is getting throttled")).error_class == "RateLimitError"


def test_relays_own_msg_and_a_fastapi_detail_are_read_through_the_gate():
    relay = litellm.RateLimitError(message="OpenAIException - 12345", model="m", llm_provider="openai")
    relay.body = {"msg": "Rate limit exceeded. Please try again later.", "error_code": 5001}
    fastapi = litellm.BadRequestError(message="OpenAIException - 12345", model="m", llm_provider="openai")
    fastapi.body = {"detail": "The request was malformed."}
    listed = litellm.BadRequestError(message="OpenAIException - 12345", model="m", llm_provider="openai")
    listed.body = {"detail": [{"loc": ["body"], "msg": "field required"}]}
    gated = litellm.BadRequestError(message="OpenAIException - 12345", model="m", llm_provider="openai")
    gated.body = {"msg": "Key sk-proj-AAAA1234 for admin@corp.com is invalid."}

    assert ErrorInfo.from_exception(relay).message == "Rate limit exceeded. Please try again later."
    assert ErrorInfo.from_exception(fastapi).message == "The request was malformed."
    assert ErrorInfo.from_exception(listed).message is None
    assert ErrorInfo.from_exception(gated).message is None


def test_the_providers_message_wins_over_relays_own_msg():
    exc = litellm.BadGatewayError(message="OpenAIException - 12345", model="m", llm_provider="openai")
    exc.body = {"msg": "An error occurred.", "provider_message": "Upstream failed."}

    assert ErrorInfo.from_exception(exc).message == "Upstream failed."
