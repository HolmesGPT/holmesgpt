"""Unit tests for the ConversationWorker task hydration logic.

``_hydrate_task_from_events`` consumes the flat chronological event list
returned by the ``get_conversation_events`` RPC: ``[{event, data, ts}, ...]``.
There is no row/seq nesting at this level — the RPC flattens all events from
all matching ConversationEvents rows into a single list ordered by
``(seq, ord)``. Turn boundaries are detected by the ``user_message`` event.
"""
from holmes.core.conversations_worker.models import ConversationTask
from holmes.core.conversations_worker.worker import ConversationWorker


def _make_worker():
    """Build a ConversationWorker without calling its __init__ so we can test
    _hydrate_task_from_events in isolation."""
    return ConversationWorker.__new__(ConversationWorker)


def test_hydrate_first_turn_ask_only():
    worker = _make_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    events = [
        {"event": "user_message", "data": {"ask": "hello"}, "ts": "2026-04-13T00:00:00Z"}
    ]
    worker._hydrate_task_from_events(task, events)
    assert task.ask == "hello"
    assert task.conversation_history is None


def test_hydrate_followup_reconstructs_history():
    worker = _make_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=2,
    )
    prev_messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    events = [
        {"event": "user_message", "data": {"ask": "hello"}, "ts": "1"},
        {
            "event": "ai_answer_end",
            "data": {"content": "hi there", "messages": prev_messages},
            "ts": "2",
        },
        {"event": "user_message", "data": {"ask": "how are you?"}, "ts": "3"},
    ]
    worker._hydrate_task_from_events(task, events)
    assert task.ask == "how are you?"
    assert task.conversation_history == prev_messages


def test_hydrate_picks_approval_required_history():
    worker = _make_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=2,
    )
    prev_messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
    ]
    events = [
        {"event": "user_message", "data": {"ask": "q"}, "ts": "0"},
        {
            "event": "approval_required",
            "data": {"messages": prev_messages, "pending_approvals": []},
            "ts": "1",
        },
        {
            "event": "user_message",
            "data": {
                "ask": "continue",
                "tool_decisions": [
                    {"tool_call_id": "x", "approved": True, "save_prefixes": None}
                ],
            },
            "ts": "2",
        },
    ]
    worker._hydrate_task_from_events(task, events)
    assert task.ask == "continue"
    assert task.conversation_history == prev_messages
    assert task.tool_decisions is not None
    assert task.enable_tool_approval is True


def test_hydrate_ignores_terminal_events_after_current_user_message():
    """A terminal event whose index is AFTER the latest user_message must not
    be picked as the history (that would be circular)."""
    worker = _make_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=2,
    )
    history_turn_1 = [{"role": "system", "content": "s"}, {"role": "user", "content": "q1"}]
    stale_history_turn_2 = [{"role": "system", "content": "should_not_be_used"}]
    events = [
        {"event": "user_message", "data": {"ask": "q1"}, "ts": "1"},
        {
            "event": "ai_answer_end",
            "data": {"content": "a1", "messages": history_turn_1},
            "ts": "2",
        },
        {"event": "user_message", "data": {"ask": "q2"}, "ts": "3"},
        # Stale terminal AFTER the latest user_message (e.g. from a prior attempt)
        {
            "event": "ai_answer_end",
            "data": {"content": "stale", "messages": stale_history_turn_2},
            "ts": "4",
        },
    ]
    worker._hydrate_task_from_events(task, events)
    assert task.ask == "q2"
    assert task.conversation_history == history_turn_1


def test_extract_last_user_ask():
    """_extract_last_user_ask walks a message history and returns the last user text."""
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "answer 1"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "answer 2"},
    ]
    assert ConversationWorker._extract_last_user_ask(history) == "second question"

    vision_history = [
        {"role": "system", "content": "sys"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look at this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
            ],
        },
    ]
    assert ConversationWorker._extract_last_user_ask(vision_history) == "look at this"

    assert ConversationWorker._extract_last_user_ask(None) is None
    assert ConversationWorker._extract_last_user_ask([]) is None


def test_hydrate_extracts_model_override():
    worker = _make_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    events = [
        {
            "event": "user_message",
            "data": {
                "ask": "hi",
                "model": "Robusta/Sonnet 4.5",
                "additional_system_prompt": "be concise",
            },
            "ts": "1",
        }
    ]
    worker._hydrate_task_from_events(task, events)
    assert task.ask == "hi"
    assert task.model == "Robusta/Sonnet 4.5"
    assert task.additional_system_prompt == "be concise"
