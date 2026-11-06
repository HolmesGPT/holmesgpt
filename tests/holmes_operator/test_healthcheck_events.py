"""Unit tests for HealthCheck event message truncation."""

from holmes_operator.handlers.healthcheck import (
    _EVENT_TRUNCATION_MARKER,
    _MAX_EVENT_MESSAGE_BYTES,
    _truncate_event_message,
)


def test_truncate_event_message_passes_short_messages_unchanged():
    msg = "Health check pass: ok"
    assert _truncate_event_message(msg) == msg


def test_truncate_event_message_caps_long_messages_with_marker():
    msg = "Health check fail: " + ("x" * 5000)
    out = _truncate_event_message(msg)
    assert len(out.encode("utf-8")) <= _MAX_EVENT_MESSAGE_BYTES
    assert out.endswith(_EVENT_TRUNCATION_MARKER)


def test_truncate_event_message_boundary_length_is_not_truncated():
    msg = "y" * _MAX_EVENT_MESSAGE_BYTES
    assert _truncate_event_message(msg) == msg


def test_truncate_event_message_caps_multibyte_messages_by_byte_length():
    msg = "你好" * 5000
    out = _truncate_event_message(msg)
    assert len(out.encode("utf-8")) <= _MAX_EVENT_MESSAGE_BYTES
    assert out.endswith(_EVENT_TRUNCATION_MARKER)


def test_truncate_event_message_never_splits_a_multibyte_character():
    # A 3-byte character straddling the byte budget must be dropped, not halved.
    msg = "z" * (_MAX_EVENT_MESSAGE_BYTES - 15) + "你" * 100
    out = _truncate_event_message(msg)
    assert len(out.encode("utf-8")) <= _MAX_EVENT_MESSAGE_BYTES
    out.encode("utf-8").decode("utf-8")
