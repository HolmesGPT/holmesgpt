"""A failed worker turn keeps the sanitized cause: error class, provider status
and the provider's reason, for customer-key and Robusta-hosted models alike."""

from unittest.mock import patch

import litellm
import pytest

from holmes.core.conversations_worker.processor import ConversationProcessor
from holmes.core.relay_refusal import RelayRefusal
from tests.core.conversations_worker.test_processor import _processor, _task
from tests.core.conversations_worker.test_processor_usage_recorder import (
    _bare_worker,
    _run,
)

GENERIC_DESCRIPTION = "An internal error occurred while processing your request"


def _error_event(worker):
    worker.dal.post_conversation_events.assert_called_once()
    (event,) = worker.dal.post_conversation_events.call_args.kwargs["events"]
    assert event["event"] == "error"
    return event["data"]


def _provider_error(cls, message, provider="bedrock"):
    return cls(message=message, model="m", llm_provider=provider)


@pytest.mark.parametrize("is_robusta_model", [False, True])
def test_provider_failure_keeps_class_status_and_reason(is_robusta_model):
    w, ai = _bare_worker()
    ai.llm.is_robusta_model = is_robusta_model
    exc = _provider_error(
        litellm.ServiceUnavailableError,
        "BedrockException - Bedrock is unable to process your request.",
    )

    _run(w, ai, consume_side_effect=exc)

    data = _error_event(w)
    expected = (
        "AI provider error: ServiceUnavailableError (503, bedrock). "
        "Bedrock is unable to process your request."
    )
    assert data["description"] == expected
    assert data["msg"] == expected
    assert data["error_class"] == "ServiceUnavailableError"
    assert data["provider_status"] == 503
    assert data["provider"] == "bedrock"
    assert "Bedrock is unable to process your request." in data["raw_error"]
    w.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1", request_sequence=1, assignee="h-test", status="failed"
    )


def test_a_message_that_is_not_plain_prose_is_dropped_but_the_code_is_kept():
    w, ai = _bare_worker()
    exc = _provider_error(
        litellm.AuthenticationError,
        "OpenAIException - Error code: 401 - {'error': {'message': 'Incorrect API key provided: "
        "sk-proj-AAAABBBBCCCCDDDD1234567890 for admin@corp.com at 10.1.2.3:8443', "
        "'type': 'invalid_request_error', 'code': 'invalid_api_key'}}",
        provider="openai",
    )

    _run(w, ai, consume_side_effect=exc)

    data = _error_event(w)
    stored = repr(data)
    for secret in ("sk-proj-AAAA", "admin@corp.com", "10.1.2.3", "Incorrect API key"):
        assert secret not in stored
    assert "raw_error" not in data
    assert data["description"] == (
        "AI provider error: AuthenticationError (401, openai, invalid_api_key)."
    )
    assert data["error_class"] == "AuthenticationError"
    assert data["provider_status"] == 401
    assert data["provider_code"] == "invalid_api_key"


def test_the_robusta_account_limit_sentence_survives_for_the_ui():
    w, ai = _bare_worker()
    ai.llm.is_robusta_model = True
    exc = _provider_error(
        litellm.RateLimitError,
        "OpenAIException - Error code: 429 - {'error': {'message': 'Your Robusta AI account limit has "
        "been reached. Contact support@robusta.dev to increase limits.', "
        "'type': 'rate_limit_exceeded', 'code': 'account_limit_reached'}}",
        provider="openai",
    )

    _run(w, ai, consume_side_effect=exc)

    data = _error_event(w)
    assert data["raw_error"] == "Your Robusta AI account limit has been reached."
    assert data["provider_code"] == "account_limit_reached"


def test_unknown_exception_keeps_only_its_class():
    w, ai = _bare_worker()

    _run(w, ai, consume_side_effect=RuntimeError("db password=hunter2 failed"))

    data = _error_event(w)
    assert data["description"] == GENERIC_DESCRIPTION
    assert data["error_class"] == "RuntimeError"
    assert "provider_status" not in data
    assert "raw_error" not in data
    assert "hunter2" not in repr(data)


def test_relay_refusal_event_also_carries_its_class_and_status():
    w, ai = _bare_worker()
    ai.llm.is_robusta_model = True

    _run(w, ai, consume_side_effect=RelayRefusal("Robusta AI is disabled.", 403))

    data = _error_event(w)
    assert data["description"] == "Robusta AI is disabled."
    assert data["error_class"] == "RelayRefusal"
    assert data["provider_status"] == 403
    assert data["raw_error"] == "Robusta AI is disabled."


def test_the_outer_failure_handler_keeps_the_class_too():
    p = _processor()
    exc = litellm.RateLimitError(
        message="OpenAIException - Rate limit reached", model="m", llm_provider="openai"
    )

    def boom(*a, **kw):
        raise exc

    with patch.object(ConversationProcessor, "_process_conversation", boom):
        p.run(_task())

    (event,) = p.dal.post_conversation_events.call_args.kwargs["events"]
    assert event["data"]["error_class"] == "RateLimitError"
    assert event["data"]["provider_status"] == 429
    assert event["data"]["description"].startswith("AI provider error: RateLimitError")
    assert event["data"]["description"] == event["data"]["msg"]


def test_a_provider_error_without_a_message_omits_raw_error():
    class Silent(litellm.APIError):
        def __str__(self):
            return ""

    w, ai = _bare_worker()

    _run(
        w,
        ai,
        consume_side_effect=Silent(
            status_code=500, message="", llm_provider="openai", model="m"
        ),
    )

    data = _error_event(w)
    assert data["provider_status"] == 500
    assert "raw_error" not in data


def test_a_relay_refusal_sentence_is_kept_as_it_is():
    w, ai = _bare_worker()
    message = "Robusta-hosted models are disabled for this account."

    _run(w, ai, consume_side_effect=RelayRefusal(message, 403))

    data = _error_event(w)
    assert data["description"] == message
    assert data["msg"] == message
    assert data["raw_error"] == message


@pytest.mark.parametrize(
    "text",
    [
        "Refused.\nTraceback (most recent call last):\n  File x",
        "word " * 100,
        "<html>" + "a" * 400 + "</html>",
    ],
)
def test_text_that_is_not_a_refusal_sentence_is_dropped(text):
    w, ai = _bare_worker()

    _run(w, ai, consume_side_effect=RelayRefusal(text, 401))

    data = _error_event(w)
    assert data["description"] == "The Robusta platform refused the request."
    assert "raw_error" not in data
