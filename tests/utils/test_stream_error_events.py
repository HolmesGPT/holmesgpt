import json

import litellm

from holmes.core.relay_refusal import RelayRefusal
from holmes.utils.stream import stream_chat_formatter


def _error_event(exc: Exception) -> dict:
    def failing():
        raise exc
        yield  # pragma: no cover

    (frame,) = list(stream_chat_formatter(failing()))
    event, data = frame.strip().split("\n", 1)
    assert event == "event: error"
    return json.loads(data.removeprefix("data: "))


def test_provider_failure_carries_class_status_and_provider():
    data = _error_event(
        litellm.ServiceUnavailableError(
            message="BedrockException - capacity", model="m", llm_provider="bedrock"
        )
    )

    assert data["error_class"] == "ServiceUnavailableError"
    assert data["provider_status"] == 503
    assert data["provider"] == "bedrock"
    assert data["error_code"] == 1


def test_rate_limit_keeps_its_code_and_gains_the_class():
    data = _error_event(
        litellm.RateLimitError(message="slow", model="m", llm_provider="openai")
    )

    assert data["error_code"] == 5204
    assert data["error_class"] == "RateLimitError"
    assert data["provider_status"] == 429


def test_relay_refusal_carries_its_class():
    data = _error_event(RelayRefusal("Not enabled", 403))

    assert data["error_class"] == "RelayRefusal"
    assert data["provider_status"] == 403


def test_unknown_failure_carries_only_its_class():
    data = _error_event(RuntimeError("boom"))

    assert data["error_class"] == "RuntimeError"
    assert "provider_status" not in data and "provider" not in data
