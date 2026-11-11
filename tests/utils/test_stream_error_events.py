import json

import litellm
import pytest

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


def test_answer_end_without_text_streams_an_empty_analysis():
    from holmes.utils.stream import StreamEvents, StreamMessage

    def stream():
        yield StreamMessage(
            event=StreamEvents.ANSWER_END,
            data={"content": None, "messages": [], "metadata": {}},
        )

    (frame,) = list(stream_chat_formatter(stream()))
    event, data = frame.strip().split("\n", 1)
    assert event == "event: ai_answer_end"
    assert json.loads(data.removeprefix("data: "))["analysis"] == ""


@pytest.mark.parametrize("content", [None, ""])
def test_empty_structured_output_answer_becomes_an_error_event(content):
    from holmes.utils.stream import StreamEvents, StreamMessage, reject_empty_structured_output

    costs = {"total_cost": 0.5}
    answer = StreamMessage(
        event=StreamEvents.ANSWER_END,
        data={"content": content, "messages": [], "metadata": {"costs": costs}, "num_llm_calls": 2},
    )

    (message,) = list(reject_empty_structured_output(iter([answer])))

    assert message.event == StreamEvents.ERROR
    assert message.data["success"] is False
    # The usage recorder reads the run's costs from the error event.
    assert message.data["metadata"]["costs"] == costs
    assert message.data["num_llm_calls"] == 2


def test_structured_output_answer_with_content_passes_through():
    from holmes.utils.stream import StreamEvents, StreamMessage, reject_empty_structured_output

    answer = StreamMessage(event=StreamEvents.ANSWER_END, data={"content": "{}"})

    assert list(reject_empty_structured_output(iter([answer]))) == [answer]
