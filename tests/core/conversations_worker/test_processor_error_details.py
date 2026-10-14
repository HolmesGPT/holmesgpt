"""A failed worker turn keeps the sanitized cause: error class, provider status
and the provider's reason, for customer-key and Robusta-hosted models alike."""

import litellm
import pytest

from holmes.core.relay_refusal import RelayRefusal
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


def _provider_error(cls, message, status, provider="bedrock"):
    return cls(message=message, model="m", llm_provider=provider)


@pytest.mark.parametrize("is_robusta_model", [False, True])
def test_provider_failure_keeps_class_status_and_reason(is_robusta_model):
    w, ai = _bare_worker()
    ai.llm.is_robusta_model = is_robusta_model
    exc = _provider_error(
        litellm.ServiceUnavailableError,
        "BedrockException - Bedrock is unable to process your request.",
        503,
    )

    _run(w, ai, consume_side_effect=exc)

    data = _error_event(w)
    assert data["description"] == GENERIC_DESCRIPTION
    assert data["error_class"] == "ServiceUnavailableError"
    assert data["provider_status"] == 503
    assert data["provider"] == "bedrock"
    assert "Bedrock is unable to process your request." in data["raw_error"]
    w.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1", request_sequence=1, assignee="h-test", status="failed"
    )


def test_customer_key_failure_never_stores_secrets_from_the_provider_message():
    w, ai = _bare_worker()
    exc = _provider_error(
        litellm.AuthenticationError,
        "OpenAIException - Incorrect API key provided: sk-proj-AAAABBBBCCCCDDDD1234567890 "
        "for admin@corp.com at 10.1.2.3:8443",
        401,
        provider="openai",
    )

    _run(w, ai, consume_side_effect=exc)

    data = _error_event(w)
    stored = repr(data)
    for secret in ("sk-proj-AAAA", "admin@corp.com", "10.1.2.3"):
        assert secret not in stored
    assert data["error_class"] == "AuthenticationError"
    assert data["provider_status"] == 401
    assert "Incorrect API key provided" in data["raw_error"]


def test_unknown_exception_keeps_only_its_class():
    w, ai = _bare_worker()

    _run(w, ai, consume_side_effect=RuntimeError("db password=hunter2 failed"))

    data = _error_event(w)
    assert data["error_class"] == "RuntimeError"
    assert "provider_status" not in data
    assert "raw_error" not in data
    assert "hunter2" not in repr(data)


def test_relay_refusal_event_also_carries_its_class_and_status():
    w, ai = _bare_worker()
    ai.llm.is_robusta_model = True

    _run(w, ai, consume_side_effect=RelayRefusal("Robusta AI is disabled.", 403))

    data = _error_event(w)
    assert data["error_class"] == "RelayRefusal"
    assert data["provider_status"] == 403
    assert data["raw_error"] == "Robusta AI is disabled."


def test_the_outer_failure_handler_keeps_the_class_too():
    from unittest.mock import patch

    from holmes.core.conversations_worker.processor import ConversationProcessor
    from tests.core.conversations_worker.test_processor import _processor, _task

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
