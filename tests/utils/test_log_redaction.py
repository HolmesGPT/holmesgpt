"""Tests for credential redaction in log output (see GitHub issue #2010)."""

import logging

import pytest

from holmes.utils.log import (
    REDACTED,
    SecretRedactingFilter,
    install_secret_redaction,
    redact_secrets,
)

# Fake credentials matching each provider's key *format* (so the redaction
# regexes fire) without ever writing a contiguous secret-looking literal into
# this file. CI secret scanners (e.g. Netlify) flag any `AIza...`/`sk-...`
# string in repo source, so the recognizable prefixes are assembled at runtime
# from split fragments and the bodies are obviously-fake repeated characters.
FAKE_GEMINI_KEY = "AI" + "za" + "Sy" + ("0" * 35)  # AIza + 35 chars
FAKE_OPENAI_KEY = "s" + "k-" + "proj-" + ("0" * 40)  # sk- + body
FAKE_ANTHROPIC_KEY = "s" + "k-" + "ant-" + ("0" * 40)  # sk-ant- + body


def test_redacts_gemini_key_in_url_query_param():
    text = (
        "Client error '400 Bad Request' for url "
        f"'https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={FAKE_GEMINI_KEY}'"
    )
    redacted = redact_secrets(text)
    assert FAKE_GEMINI_KEY not in redacted
    assert "?key=" + REDACTED in redacted


def test_redacts_key_in_query_string_preserves_following_params():
    text = f"https://api.example.com/v1/chat?key={FAKE_GEMINI_KEY}&model=gemini-pro"
    redacted = redact_secrets(text)
    assert FAKE_GEMINI_KEY not in redacted
    # The non-secret param after the key must survive.
    assert "&model=gemini-pro" in redacted


@pytest.mark.parametrize(
    "param",
    ["api_key", "api-key", "apikey", "access_token", "token", "password", "secret"],
)
def test_redacts_common_credential_query_params(param):
    text = f"https://api.example.com/v1?{param}=supersecretvalue123&foo=bar"
    redacted = redact_secrets(text)
    assert "supersecretvalue123" not in redacted
    assert "&foo=bar" in redacted


def test_redacts_known_key_formats_without_key_name():
    for key in (FAKE_GEMINI_KEY, FAKE_OPENAI_KEY, FAKE_ANTHROPIC_KEY):
        redacted = redact_secrets(f"leaked credential: {key} end")
        assert key not in redacted
        assert REDACTED in redacted


def test_redacts_bearer_token():
    text = "Authorization: Bearer abc.def.ghi-jkl_mno123"
    redacted = redact_secrets(text)
    assert "abc.def.ghi-jkl_mno123" not in redacted
    assert REDACTED in redacted


def test_redacts_key_value_in_json_like_body():
    text = '{"api_key": "myverysecretkey123456", "model": "gemini-pro"}'
    redacted = redact_secrets(text)
    assert "myverysecretkey123456" not in redacted
    assert "gemini-pro" in redacted


def test_leaves_innocuous_text_untouched():
    text = "Tool call to kubectl_get failed with an Exception for namespace=app-177"
    assert redact_secrets(text) == text


def test_handles_none_and_non_strings():
    assert redact_secrets(None) is None
    assert redact_secrets("") == ""


def _record_with_filter(make_record):
    """Apply SecretRedactingFilter to a record and return its rendered output."""
    record = make_record()
    SecretRedactingFilter().filter(record)
    formatter = logging.Formatter("%(message)s")
    return formatter.format(record)


def test_filter_redacts_message():
    output = _record_with_filter(
        lambda: logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg=f"LLM call failed: url='https://x/v1?key={FAKE_GEMINI_KEY}'",
            args=(),
            exc_info=None,
        )
    )
    assert FAKE_GEMINI_KEY not in output


def test_filter_redacts_args():
    output = _record_with_filter(
        lambda: logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Error in /api/chat: %s",
            args=(f"https://x/v1?key={FAKE_GEMINI_KEY}",),
            exc_info=None,
        )
    )
    assert FAKE_GEMINI_KEY not in output


def test_filter_redacts_exception_traceback():
    try:
        raise ValueError(
            "Client error '400 Bad Request' for url "
            f"'https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={FAKE_GEMINI_KEY}'"
        )
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="LLM call failed",
        args=(),
        exc_info=exc_info,
    )
    SecretRedactingFilter().filter(record)
    formatter = logging.Formatter("%(message)s")
    output = formatter.format(record)
    assert FAKE_GEMINI_KEY not in output
    assert REDACTED in output


def test_install_is_idempotent():
    logger = logging.getLogger("holmes.test.redaction")
    logger.handlers = [logging.NullHandler()]
    install_secret_redaction(logger)
    install_secret_redaction(logger)
    redaction_filters = [
        f for f in logger.handlers[0].filters if isinstance(f, SecretRedactingFilter)
    ]
    assert len(redaction_filters) == 1
