"""Edge-case tests for ConversationWorker input handling.

These tests compare the worker's behavior to the /api/chat endpoint's
Pydantic gating: malformed/missing inputs must result in a clean failure
(error event + status=failed), never a silent re-run or empty-LLM-call.

Covered scenarios:
  1. Conversation with no events at all.
  2. user_message with empty-string ask and no tool_decisions.
  3. user_message with no ask and no tool_decisions / frontend_tool_results.
  4. Previous terminal event (ai_answer_end / approval_required) present
     but no new user_message for this turn — the prior user_message is
     "already answered" and must not be re-processed.
"""
import threading
from collections import deque
from unittest.mock import MagicMock, patch

from holmes.core.conversations_worker.models import ConversationTask
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


def _task():
    return ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )


def _assert_failed_with_no_question(worker, task):
    """The conversation should be failed with a 'No user question' error event,
    and the LLM pipeline should NOT have been invoked."""
    # Status flipped to failed
    status_calls = [
        c for c in worker.dal.update_conversation_status.call_args_list
        if c.kwargs.get("status") == "failed"
    ]
    assert status_calls, (
        f"Expected update_conversation_status(status='failed') to be called. "
        f"Actual calls: {worker.dal.update_conversation_status.call_args_list}"
    )

    # Error event posted with the expected description
    post_calls = worker.dal.post_conversation_events.call_args_list
    assert post_calls, "Expected post_conversation_events to be called with an error event"
    events = post_calls[0].kwargs["events"]
    assert events[0]["event"] == "error"
    assert "No user question" in events[0]["data"]["description"]


def _run_process(worker, task, events):
    """Invoke _process_conversation with the given events returned from the DAL.
    Patches _run_chat_and_publish so the test fails loudly if the LLM pipeline
    is invoked (which would mean the guard didn't fire)."""
    worker.dal.get_conversation_events = MagicMock(return_value=events)
    with patch.object(
        ConversationWorker, "_run_chat_and_publish"
    ) as run_chat:
        worker._process_conversation(task)
    return run_chat


def test_no_events_fails_cleanly():
    worker = _bare_worker()
    task = _task()
    run_chat = _run_process(worker, task, [])
    _assert_failed_with_no_question(worker, task)
    run_chat.assert_not_called()


def test_empty_ask_fails_cleanly():
    worker = _bare_worker()
    task = _task()
    events = [
        {"event": "user_message", "data": {"ask": ""}, "ts": "1"},
    ]
    run_chat = _run_process(worker, task, events)
    _assert_failed_with_no_question(worker, task)
    run_chat.assert_not_called()


def test_user_message_without_ask_or_decisions_fails():
    """user_message with no ask, no tool_decisions, no frontend_tool_results —
    there's no actionable input. Must fail cleanly."""
    worker = _bare_worker()
    task = _task()
    events = [
        {"event": "user_message", "data": {"model": "whatever"}, "ts": "1"},
    ]
    run_chat = _run_process(worker, task, events)
    _assert_failed_with_no_question(worker, task)
    run_chat.assert_not_called()


def test_already_answered_user_message_fails():
    """A terminal event AFTER the latest user_message means that user_message
    was already processed. The worker must NOT re-run the stale question —
    it must fail cleanly so the missing new user_message is surfaced."""
    worker = _bare_worker()
    task = _task()
    events = [
        {"event": "user_message", "data": {"ask": "original"}, "ts": "1"},
        {
            "event": "ai_answer_end",
            "data": {"content": "answered", "messages": [{"role": "system", "content": "s"}]},
            "ts": "2",
        },
    ]
    run_chat = _run_process(worker, task, events)
    _assert_failed_with_no_question(worker, task)
    run_chat.assert_not_called()


def test_already_approved_user_message_fails():
    """Same scenario with approval_required instead of ai_answer_end — the
    previous turn is waiting for user input, but no new user_message arrived."""
    worker = _bare_worker()
    task = _task()
    events = [
        {"event": "user_message", "data": {"ask": "original"}, "ts": "1"},
        {
            "event": "approval_required",
            "data": {
                "messages": [{"role": "system", "content": "s"}],
                "pending_approvals": [],
            },
            "ts": "2",
        },
    ]
    run_chat = _run_process(worker, task, events)
    _assert_failed_with_no_question(worker, task)
    run_chat.assert_not_called()


def test_followup_with_tool_decisions_is_processed():
    """Positive control: valid resume-only flow (new user_message with
    tool_decisions, after an approval_required) must be processed, not failed."""
    worker = _bare_worker()
    task = _task()
    prev_messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "original"},
    ]
    events = [
        {"event": "user_message", "data": {"ask": "original"}, "ts": "1"},
        {
            "event": "approval_required",
            "data": {"messages": prev_messages, "pending_approvals": []},
            "ts": "2",
        },
        {
            "event": "user_message",
            "data": {
                "tool_decisions": [
                    {"tool_call_id": "x", "approved": True, "save_prefixes": None}
                ]
            },
            "ts": "3",
        },
    ]
    run_chat = _run_process(worker, task, events)
    # Should NOT have failed — should have invoked the LLM pipeline
    failed_calls = [
        c for c in worker.dal.update_conversation_status.call_args_list
        if c.kwargs.get("status") == "failed"
    ]
    assert not failed_calls, f"Expected no failure, got {failed_calls}"
    run_chat.assert_called_once()
