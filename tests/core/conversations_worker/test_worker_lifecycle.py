"""Unit tests for worker lifecycle / claim-loop / error handling."""
import threading
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.conversations_worker.models import (
    ConversationReassignedError,
    ConversationTask,
)
from holmes.core.conversations_worker.worker import ConversationWorker


def _bare_worker():
    w = ConversationWorker.__new__(ConversationWorker)
    w.dal = MagicMock()
    w.dal.enabled = True
    w.dal.update_conversation_status = MagicMock(return_value=True)
    w.config = MagicMock()
    w.chat_function = MagicMock()
    w.holmes_id = "h-test"
    w._running = True
    w._claim_thread = None
    w._notify_event = threading.Event()
    w._executor = MagicMock()
    w._active_conversation_ids = set()
    w._active_lock = threading.Lock()
    w._queued_tasks = deque()
    w._queued_lock = threading.Lock()
    w._dispatch_lock = threading.Lock()
    w._realtime_manager = None
    return w


def test_build_task_from_conversation_row_parses_required_fields():
    w = _bare_worker()
    row = {
        "conversation_id": "c1",
        "account_id": "a1",
        "cluster_id": "cl1",
        "origin": "chat",
        "request_sequence": 3,
        "metadata": {"foo": "bar"},
        "title": "hello",
    }
    task = w._build_task_from_conversation_row(row)
    assert task is not None
    assert task.conversation_id == "c1"
    assert task.request_sequence == 3
    assert task.metadata == {"foo": "bar"}
    assert task.title == "hello"


def test_build_task_from_conversation_row_tolerates_missing_fields():
    w = _bare_worker()
    row = {"conversation_id": "c1", "account_id": "a1", "cluster_id": "cl1"}
    task = w._build_task_from_conversation_row(row)
    assert task is not None
    assert task.request_sequence == 1
    assert task.origin == "chat"


def test_build_task_from_conversation_row_returns_none_on_bad_input():
    w = _bare_worker()
    task = w._build_task_from_conversation_row({})  # missing required fields
    assert task is None


def test_try_claim_and_dispatch_claims_all_and_queues():
    """Claiming should always happen regardless of capacity.
    Tasks go into _queued_tasks first, then dispatched up to capacity."""
    w = _bare_worker()
    w.dal.claim_conversations.return_value = [
        {
            "conversation_id": "c1",
            "account_id": "a1",
            "cluster_id": "cl1",
            "origin": "chat",
            "request_sequence": 1,
            "metadata": {},
        },
        {
            "conversation_id": "c2",
            "account_id": "a1",
            "cluster_id": "cl1",
            "origin": "chat",
            "request_sequence": 1,
            "metadata": {},
        },
    ]
    w._try_claim_and_dispatch()
    # Both should have been submitted to executor (capacity = default 5)
    assert w._executor.submit.call_count == 2
    # Both should be in active set
    assert "c1" in w._active_conversation_ids
    assert "c2" in w._active_conversation_ids
    # update_conversation_status called twice to transition to running
    assert w.dal.update_conversation_status.call_count == 2


def test_try_claim_and_dispatch_queues_when_at_capacity(monkeypatch):
    """When at capacity, tasks stay in the queued pool, not submitted."""
    w = _bare_worker()
    monkeypatch.setattr(
        "holmes.core.conversations_worker.worker.CONVERSATION_WORKER_MAX_CONCURRENT",
        1,
    )
    # Already have one active conversation
    w._active_conversation_ids = {"existing"}
    w.dal.claim_conversations.return_value = [
        {
            "conversation_id": "c1",
            "account_id": "a1",
            "cluster_id": "cl1",
            "origin": "chat",
            "request_sequence": 1,
            "metadata": {},
        }
    ]
    w._try_claim_and_dispatch()
    # Claim should still happen (no capacity check before claiming)
    w.dal.claim_conversations.assert_called_once()
    # But the task should NOT be submitted to executor
    w._executor.submit.assert_not_called()
    # It should be in the queued tasks
    assert len(w._queued_tasks) == 1
    assert w._queued_tasks[0].conversation_id == "c1"


def test_dispatch_queued_transitions_to_running():
    """_dispatch_queued should call update_conversation_status(running) and submit."""
    w = _bare_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    w._queued_tasks.append(task)
    w._dispatch_queued()
    w.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1",
        request_sequence=1,
        assignee="h-test",
        status="running",
    )
    w._executor.submit.assert_called_once()
    assert "c1" in w._active_conversation_ids


def test_dispatch_queued_skips_if_transition_fails():
    """If update_conversation_status returns False, task is not submitted."""
    w = _bare_worker()
    w.dal.update_conversation_status.return_value = False
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    w._queued_tasks.append(task)
    w._dispatch_queued()
    w._executor.submit.assert_not_called()
    assert "c1" not in w._active_conversation_ids


def test_dispatch_queued_handles_mismatch_during_transition():
    """If the queued→running transition raises ConversationReassignedError
    (e.g. stop_conversation bumped request_sequence while queued), the task
    must be skipped — not submitted to executor."""
    w = _bare_worker()
    w.dal.update_conversation_status.side_effect = ConversationReassignedError(
        "MISMATCH Request sequence expected 1, got 2"
    )
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    w._queued_tasks.append(task)
    w._dispatch_queued()
    w._executor.submit.assert_not_called()
    assert "c1" not in w._active_conversation_ids


def test_process_conversation_safe_marks_failed_on_exception():
    w = _bare_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )

    def boom(*a, **kw):
        raise RuntimeError("synthetic failure")

    with patch.object(ConversationWorker, "_process_conversation", boom):
        w._process_conversation_safe(task)

    # Error event should be posted before marking as failed
    w.dal.post_conversation_events.assert_called_once()
    call_kwargs = w.dal.post_conversation_events.call_args[1]
    assert call_kwargs["conversation_id"] == "c1"
    error_events = call_kwargs["events"]
    assert error_events[0]["event"] == "error"
    # The error event must use a generic message, not the raw exception text
    desc = error_events[0]["data"]["description"]
    assert "synthetic failure" not in desc, "Raw exception text must not leak into error events"
    assert "internal error" in desc.lower()

    w.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1",
        request_sequence=1,
        assignee="h-test",
        status="failed",
    )
    # active conversation cleared
    assert "c1" not in w._active_conversation_ids


def test_process_conversation_safe_clears_active_on_success():
    w = _bare_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    with patch.object(ConversationWorker, "_process_conversation", lambda self, t: None):
        w._process_conversation_safe(task)

    assert "c1" not in w._active_conversation_ids


def test_process_conversation_safe_no_status_update_on_reassignment():
    """On ConversationReassignedError the worker must NOT call
    update_conversation_status — the conversation's state is already
    being handled by whoever reassigned it."""
    w = _bare_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )

    def boom(*a, **kw):
        raise ConversationReassignedError("x")

    with patch.object(ConversationWorker, "_process_conversation", boom):
        w._process_conversation_safe(task)

    w.dal.update_conversation_status.assert_not_called()
    w.dal.post_conversation_events.assert_not_called()
    assert "c1" not in w._active_conversation_ids


def test_process_conversation_safe_dispatches_queued_after_completion():
    """After a conversation finishes, the worker should try to dispatch queued tasks."""
    w = _bare_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    # Pre-queue a task that should be dispatched after c1 finishes
    next_task = ConversationTask(
        conversation_id="c2",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    w._queued_tasks.append(next_task)

    with patch.object(ConversationWorker, "_process_conversation", lambda self, t: None):
        w._process_conversation_safe(task)

    # c2 should have been dispatched (transition to running + submit)
    w.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c2",
        request_sequence=1,
        assignee="h-test",
        status="running",
    )
    w._executor.submit.assert_called_once()
    assert "c2" in w._active_conversation_ids


def test_notify_event_wakes_claim_loop():
    """The claim loop should wake quickly when notify_event is set.

    When _realtime_manager is set, the initial claim is deferred until
    the SUBSCRIBED callback fires on_new_pending (which sets _notify_event).
    This test simulates that by setting the event externally.
    """
    w = _bare_worker()
    w._realtime_manager = MagicMock()
    w._realtime_manager.is_connected.return_value = True

    call_count = {"n": 0}

    def fake_claim():
        call_count["n"] += 1
        if call_count["n"] >= 1:
            w._running = False

    w._try_claim_and_dispatch = fake_claim

    t = threading.Thread(target=w._claim_loop)
    t.start()
    # Simulate the SUBSCRIBED callback firing on_new_pending
    w._notify_event.set()
    t.join(timeout=3)
    assert not t.is_alive(), "claim loop did not exit after notify"
    assert call_count["n"] == 1


def test_claim_loop_initial_claim_without_realtime():
    """When _realtime_manager is None, the claim loop does an immediate
    initial claim without waiting for a notification."""
    w = _bare_worker()
    w._realtime_manager = None

    call_count = {"n": 0}

    def fake_claim():
        call_count["n"] += 1
        w._running = False

    w._try_claim_and_dispatch = fake_claim

    t = threading.Thread(target=w._claim_loop)
    t.start()
    t.join(timeout=3)
    assert not t.is_alive()
    assert call_count["n"] == 1
