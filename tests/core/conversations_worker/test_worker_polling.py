"""Unit tests for the ConversationWorker's realtime-gated polling logic."""
from unittest.mock import MagicMock

from holmes.common.env_vars import CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITH_REALTIME
from holmes.core.conversations_worker.worker import ConversationWorker


def _make_worker_with_rt(connected: bool):
    worker = ConversationWorker.__new__(ConversationWorker)
    rt = MagicMock()
    rt.is_connected.return_value = connected
    worker._realtime_manager = rt
    return worker


def test_realtime_connected_returns_true_when_manager_connected():
    worker = _make_worker_with_rt(True)
    assert worker._realtime_connected() is True


def test_realtime_connected_false_when_no_manager():
    worker = ConversationWorker.__new__(ConversationWorker)
    worker._realtime_manager = None
    assert worker._realtime_connected() is False


def test_realtime_connected_false_when_manager_disconnected():
    worker = _make_worker_with_rt(False)
    assert worker._realtime_connected() is False


def test_realtime_connected_false_when_is_connected_raises():
    worker = ConversationWorker.__new__(ConversationWorker)
    rt = MagicMock()
    rt.is_connected.side_effect = RuntimeError("boom")
    worker._realtime_manager = rt
    assert worker._realtime_connected() is False


def test_connected_poll_is_reasonable_safety_net():
    # When realtime is connected, we still poll as a safety net for missed
    # notifications. The interval should be large enough to avoid spam but
    # small enough that missed events are caught in reasonable time.
    assert 30 <= CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITH_REALTIME <= 600
