"""Mid-turn follow-ups (ROB-1499).

A user message posted while a turn runs must reach the model inside that turn,
and a turn must not complete while such a message is unread. These tests pin:

* hydration: a ``mid_turn`` user_message never opens a turn; the ones queued
  behind the opening message are fed first, and ``consumed_seq`` tracks them;
* the broadcast route from RealtimeWorker to the running worker's signal;
* ``MidTurnFollowups``: queued delivery, signal-driven fetch, seq filtering,
  compacted rows included, read failures swallowed;
* ``call_stream``: messages are appended only at step boundaries, including
  when the model was about to finish;
* the DAL contract: ``_consumed_seq`` only when given, PENDING_FOLLOWUP is
  ``PendingFollowupError`` and is not retried;
* the worker's finalize loop: a refused completion runs another round from
  the captured history, bounded, and approval pauses are never guarded.
"""
import threading
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.conversations_worker.followups import (
    MID_TURN_NOTE,
    MidTurnFollowups,
    TerminalMessagesCapture,
    format_mid_turn_user_message,
    is_mid_turn_user_message,
)
from holmes.core.conversations_worker.models import (
    ConversationReassignedError,
    ConversationTask,
    PendingFollowupError,
)
from holmes.core.conversations_worker.realtime_manager import RealtimeWorker
from holmes.core.conversations_worker.processor import (
    MAX_FOLLOWUP_CONTINUATIONS,
    ConversationProcessor,
    _CompletionRefused,
)
from holmes.core.models import ChatRequest
from holmes.core.supabase_dal import SupabaseDal
from holmes.utils.stream import StreamEvents, StreamMessage


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _task(**kwargs):
    defaults = dict(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    defaults.update(kwargs)
    return ConversationTask(**defaults)


def _bare_worker():
    w = ConversationProcessor.__new__(ConversationProcessor)
    w.dal = MagicMock()
    w.dal.enabled = True
    w.dal.update_conversation_status = MagicMock(return_value=True)
    w.dal.get_global_instructions_for_account = MagicMock(return_value=None)
    w.config = MagicMock()
    ai = MagicMock()
    ai.llm = MagicMock()
    ai.llm.model = "gpt-4.1"
    ai.llm.is_robusta_model = False
    w.config.create_toolcalling_llm = MagicMock(return_value=ai)
    w.config.get_skill_catalog = MagicMock(return_value=[])
    w.config.cluster_name = "cl1"
    w.chat_function = MagicMock()
    w.holmes_id = "h-test"
    w._running = True
    w._claim_thread = None
    w._notify_event = threading.Event()
    w._saturated_since = None
    w._saturation_logged = False
    w._last_stuck_warn = None
    w._executor = MagicMock()
    w._active_conversation_ids = {}
    w._active_lock = threading.Lock()
    w._dispatch_lock = threading.Lock()
    w._realtime_manager = None
    w._followup_signals = {}
    w._followup_lock = threading.Lock()
    w.mid_turn_followup_supported = True
    return w, ai


def _dal():
    dal = SupabaseDal.__new__(SupabaseDal)
    dal.enabled = True
    dal.account_id = "a1"
    dal.client = MagicMock()
    return dal


def _ev(event, data=None, seq=None, ts="1"):
    out = {"event": event, "data": data or {}, "ts": ts}
    if seq is not None:
        out["seq"] = seq
    return out


# ---------------------------------------------------------------------------
# formatting / classification
# ---------------------------------------------------------------------------


def test_format_mid_turn_user_message_is_a_user_turn_with_the_note():
    msg = format_mid_turn_user_message("check the logs too")
    assert msg["role"] == "user"
    assert msg["content"].startswith(MID_TURN_NOTE)
    assert msg["content"].endswith("check the logs too")


@pytest.mark.parametrize(
    "event,expected",
    [
        (_ev("user_message", {"ask": "x", "mid_turn": True}), True),
        (_ev("user_message", {"ask": "x"}), False),
        (_ev("user_message", {"mid_turn": True}), False),
        (_ev("user_message", {"mid_turn": True, "ask": ""}), False),
        (_ev("ai_message", {"ask": "x", "mid_turn": True}), False),
    ],
)
def test_is_mid_turn_user_message(event, expected):
    assert is_mid_turn_user_message(event) is expected


# ---------------------------------------------------------------------------
# hydration
# ---------------------------------------------------------------------------


def test_hydrate_mid_turn_message_never_opens_a_turn():
    """After stop + new question, the newest *ordinary* message opens the turn;
    a mid-turn message that came later must not be mistaken for it."""
    w, _ = _bare_worker()
    task = _task()
    events = [
        _ev("user_message", {"ask": "first"}, seq=1),
        _ev("user_message", {"ask": "steer", "mid_turn": True}, seq=2),
    ]
    w._hydrate_task_from_events(task, events)
    assert task.user_message_data["ask"] == "first"
    assert task.queued_user_messages == [{"ask": "steer", "mid_turn": True}]
    assert task.consumed_seq == 2


def test_hydrate_queues_only_unanswered_mid_turn_messages():
    w, _ = _bare_worker()
    task = _task(request_sequence=2)
    history = [{"role": "user", "content": "first"}]
    events = [
        _ev("user_message", {"ask": "first"}, seq=1),
        _ev("user_message", {"ask": "old steer", "mid_turn": True}, seq=2),
        _ev("ai_answer_end", {"messages": history}, seq=3),
        _ev("user_message", {"ask": "second"}, seq=4),
        _ev("user_message", {"ask": "new steer", "mid_turn": True}, seq=5),
    ]
    w._hydrate_task_from_events(task, events)
    assert task.user_message_data["ask"] == "second"
    assert task.conversation_history == history
    assert [m["ask"] for m in task.queued_user_messages] == ["new steer"]
    assert task.consumed_seq == 5


def test_hydrate_consumed_seq_is_none_when_rpc_reports_no_seq():
    """An older database flattens events without seq: tracking is off."""
    w, _ = _bare_worker()
    task = _task()
    w._hydrate_task_from_events(task, [_ev("user_message", {"ask": "q"})])
    assert task.user_message_data["ask"] == "q"
    assert task.consumed_seq is None
    assert task.queued_user_messages == []


def test_hydrate_answered_turn_leaves_queue_empty():
    w, _ = _bare_worker()
    task = _task()
    events = [
        _ev("user_message", {"ask": "q"}, seq=1),
        _ev("user_message", {"ask": "steer", "mid_turn": True}, seq=2),
        _ev("ai_answer_end", {"messages": [{"role": "user", "content": "q"}]}, seq=3),
    ]
    w._hydrate_task_from_events(task, events)
    assert task.user_message_data == {}
    assert task.queued_user_messages == []


# ---------------------------------------------------------------------------
# broadcast routing
# ---------------------------------------------------------------------------


def test_notify_followup_sets_only_the_registered_signal():
    w, _ = _bare_worker()
    signal = w._register_followup_signal(("c1", 1))
    assert w.notify_followup("c-other") is False
    assert not signal.is_set()
    assert w.notify_followup("c1") is True
    assert signal.is_set()
    w._unregister_followup_signal(("c1", 1))
    assert w.notify_followup("c1") is False


def test_notify_followup_wakes_all_concurrent_turns_of_a_conversation():
    """Two request sequences of one conversation each have their own signal,
    keyed by active_key; the broadcast carries only the conversation_id and
    must wake both, and unregistering one leaves the other intact."""
    w, _ = _bare_worker()
    s1 = w._register_followup_signal(("c1", 1))
    s2 = w._register_followup_signal(("c1", 2))
    assert w.notify_followup("c1") is True
    assert s1.is_set() and s2.is_set()
    s1.clear()
    s2.clear()
    w._unregister_followup_signal(("c1", 1))
    assert w.notify_followup("c1") is True
    assert not s1.is_set()
    assert s2.is_set()


def test_notify_followup_ignores_empty_ids_and_missing_registry():
    w = ConversationProcessor.__new__(ConversationProcessor)
    assert w.notify_followup(None) is False
    assert w.notify_followup("c1") is False


def test_realtime_worker_routes_conversation_followup_to_the_worker():
    dal = MagicMock()
    dal.url = "https://sp.example"
    dal.account_id = "acc-1"
    dal.cluster = "cluster-1"
    worker = MagicMock()
    rt = RealtimeWorker(
        dal=dal,
        holmes_id="h",
        on_new_pending=MagicMock(),
        on_followup=worker.notify_followup,
    )
    assert rt.on_followup is worker.notify_followup

    rt._on_followup_broadcast(
        {"event": "conversation_followup", "payload": {"conversation_id": "c9"}}
    )
    worker.notify_followup.assert_called_once_with("c9")

    worker.notify_followup.reset_mock()
    rt._on_followup_broadcast({"conversation_id": "c10"})
    worker.notify_followup.assert_called_once_with("c10")


def test_realtime_worker_followup_callback_swallows_errors():
    dal = MagicMock()
    dal.url = "https://sp.example"
    dal.account_id = "acc-1"
    dal.cluster = "cluster-1"
    on_followup = MagicMock(side_effect=RuntimeError("boom"))
    rt = RealtimeWorker(
        dal=dal, holmes_id="h", on_new_pending=MagicMock(), on_followup=on_followup
    )
    rt._on_followup_broadcast({"payload": {"conversation_id": "c1"}})
    on_followup.assert_called_once()


# ---------------------------------------------------------------------------
# MidTurnFollowups
# ---------------------------------------------------------------------------


def test_followups_deliver_queued_messages_once():
    f = MidTurnFollowups(
        dal=MagicMock(),
        conversation_id="c1",
        signal=threading.Event(),
        consumed_seq=2,
        queued=[{"ask": "a", "mid_turn": True}, {"ask": "b", "mid_turn": True}],
    )
    first = f.pending_user_messages()
    assert [m["content"].split("\n\n")[-1] for m in first] == ["a", "b"]
    assert all(m["role"] == "user" for m in first)
    assert f.pending_user_messages() == []
    assert f.delivered_count == 2


def test_followups_fetch_on_signal_and_advance_consumed_seq():
    dal = MagicMock()
    dal.get_conversation_events.return_value = [
        _ev("user_message", {"ask": "q"}, seq=1),
        _ev("start_tool_calling", {}, seq=2),
        _ev("user_message", {"ask": "steer one", "mid_turn": True}, seq=3),
        _ev("user_message", {"ask": "steer two", "mid_turn": True}, seq=4),
    ]
    signal = threading.Event()
    f = MidTurnFollowups(dal=dal, conversation_id="c1", signal=signal, consumed_seq=1)

    assert f.pending_user_messages() == []
    dal.get_conversation_events.assert_not_called()

    signal.set()
    out = f.pending_user_messages()
    assert [m["content"].split("\n\n")[-1] for m in out] == ["steer one", "steer two"]
    assert f.consumed_seq == 4
    assert not signal.is_set()
    dal.get_conversation_events.assert_called_once_with(
        "c1", include_compacted=True, min_seq=2, raise_on_error=True
    )


def test_followups_ignore_rows_at_or_below_consumed_and_non_mid_turn():
    dal = MagicMock()
    dal.get_conversation_events.return_value = [
        _ev("user_message", {"ask": "stale", "mid_turn": True}, seq=3),
        _ev("user_message", {"ask": "ordinary"}, seq=5),
        _ev("user_message", {"mid_turn": True}, seq=6),
        _ev("user_message", {"ask": "no seq", "mid_turn": True}),
    ]
    f = MidTurnFollowups(dal=dal, conversation_id="c1", signal=threading.Event(), consumed_seq=3)
    assert f.fetch_new() == []
    assert f.consumed_seq == 3


def test_followups_disabled_without_seq_tracking_or_flag():
    dal = MagicMock()
    signal = threading.Event()
    signal.set()
    no_seq = MidTurnFollowups(dal=dal, conversation_id="c1", signal=signal, consumed_seq=None)
    assert no_seq.enabled is False
    assert no_seq.pending_user_messages() == []
    off = MidTurnFollowups(
        dal=dal, conversation_id="c1", signal=signal, consumed_seq=1, enabled=False
    )
    assert off.pending_user_messages() == []
    dal.get_conversation_events.assert_not_called()


def test_followups_poll_the_db_when_the_wake_never_came():
    """The realtime socket can be down for a whole turn; the step boundary
    then reads the DB on a timer instead, and not before the interval."""
    dal = MagicMock()
    dal.get_conversation_events.return_value = [
        _ev("user_message", {"ask": "late", "mid_turn": True}, seq=2),
    ]
    now = {"t": 100.0}
    f = MidTurnFollowups(
        dal=dal,
        conversation_id="c1",
        signal=threading.Event(),
        consumed_seq=1,
        poll_interval_seconds=10,
        clock=lambda: now["t"],
    )
    now["t"] = 105.0
    assert f.pending_user_messages() == []
    dal.get_conversation_events.assert_not_called()

    now["t"] = 110.0
    out = f.pending_user_messages()
    assert len(out) == 1 and out[0]["content"].endswith("late")
    assert f.consumed_seq == 2

    # The fetch reset the timer: the next boundary inside the interval is quiet.
    now["t"] = 115.0
    assert f.pending_user_messages() == []
    assert dal.get_conversation_events.call_count == 1


def test_followups_poll_disabled_by_default():
    dal = MagicMock()
    now = {"t": 0.0}
    f = MidTurnFollowups(
        dal=dal, conversation_id="c1", signal=threading.Event(), consumed_seq=1, clock=lambda: now["t"]
    )
    now["t"] = 1e9
    assert f.pending_user_messages() == []
    dal.get_conversation_events.assert_not_called()


def test_followups_read_failure_is_swallowed():
    dal = MagicMock()
    dal.get_conversation_events.side_effect = RuntimeError("db down")
    f = MidTurnFollowups(dal=dal, conversation_id="c1", signal=threading.Event(), consumed_seq=1)
    assert f.fetch_new() == []
    assert f.consumed_seq == 1


def test_terminal_messages_capture_remembers_last_terminal_history():
    history = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    stream = iter(
        [
            StreamMessage(event=StreamEvents.AI_MESSAGE, data={"content": "x"}),
            StreamMessage(event=StreamEvents.ANSWER_END, data={"messages": history}),
        ]
    )
    cap = TerminalMessagesCapture(stream, (StreamEvents.ANSWER_END,))
    assert [m.event for m in cap] == [StreamEvents.AI_MESSAGE, StreamEvents.ANSWER_END]
    assert cap.messages == history


# ---------------------------------------------------------------------------
# DAL contract
# ---------------------------------------------------------------------------


def test_update_status_omits_consumed_seq_when_not_given():
    dal = _dal()
    dal.client.rpc.return_value.execute.return_value = MagicMock(data=True)
    assert dal.update_conversation_status("c1", 1, "h", "completed") is True
    params = dal.client.rpc.call_args[0][1]
    assert "_consumed_seq" not in params


def test_update_status_sends_consumed_seq_when_given():
    dal = _dal()
    dal.client.rpc.return_value.execute.return_value = MagicMock(data=True)
    assert dal.update_conversation_status("c1", 1, "h", "completed", consumed_seq=7) is True
    params = dal.client.rpc.call_args[0][1]
    assert params["_consumed_seq"] == 7


def test_update_status_pending_followup_is_raised_and_not_retried():
    dal = _dal()
    dal.client.rpc.return_value.execute.side_effect = Exception(
        "PENDING_FOLLOWUP Conversation c1 has an unconsumed user message at seq 9 (consumed up to 7)"
    )
    with pytest.raises(PendingFollowupError):
        dal.update_conversation_status("c1", 1, "h", "completed", consumed_seq=7)
    assert dal.client.rpc.call_count == 1


def test_update_status_mismatch_still_reassigned():
    dal = _dal()
    dal.client.rpc.return_value.execute.side_effect = Exception(
        "MISMATCH Request sequence expected 2, got 1"
    )
    with pytest.raises(ConversationReassignedError):
        dal.update_conversation_status("c1", 1, "h", "completed", consumed_seq=7)


def test_supports_mid_turn_followup_probe():
    from postgrest.exceptions import APIError

    dal = _dal()
    dal.client.rpc.return_value.execute.return_value = MagicMock(data=True)
    assert dal.supports_mid_turn_followup() is True

    dal.client.rpc.return_value.execute.side_effect = APIError(
        {"code": "PGRST202", "message": "Could not find the function public.supports_mid_turn_followup"}
    )
    assert dal.supports_mid_turn_followup() is False

    dal.client.rpc.return_value.execute.side_effect = ConnectionError("down")
    assert dal.supports_mid_turn_followup() is None


# ---------------------------------------------------------------------------
# call_stream: messages join only at step boundaries
# ---------------------------------------------------------------------------


def _llm_response(content=None, tool_calls=None):
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    message.reasoning_content = None
    message.model_dump.return_value = {"role": "assistant", "content": content}
    response = MagicMock()
    response.choices = [MagicMock(message=message, finish_reason="stop")]
    response.usage = None
    return response


def _tool_calling_llm(responses):
    from holmes.core.tool_calling_llm import ToolCallingLLM

    llm = MagicMock()
    llm.completion.side_effect = responses
    llm.model = "gpt-4.1"
    executor = MagicMock()
    executor.get_all_tools_openai_format.return_value = []
    tcl = ToolCallingLLM(tool_executor=executor, max_steps=10, llm=llm, tool_results_dir="/tmp/holmes-test")
    return tcl, llm


def _passthrough_limiter(messages, **_kwargs):
    from holmes.core.llm import ContextWindowUsage
    from holmes.core.llm_usage import RequestStats
    from holmes.core.truncation.input_context_window_limiter import (
        ContextWindowLimiterOutput,
    )

    return ContextWindowLimiterOutput(
        metadata={},
        messages=list(messages),
        events=[],
        max_context_size=128000,
        maximum_output_token=4096,
        tokens=ContextWindowUsage(
            total_tokens=1,
            system_tokens=0,
            tools_to_call_tokens=0,
            tools_tokens=0,
            user_tokens=0,
            assistant_tokens=0,
            other_tokens=0,
        ),
        conversation_history_compacted=False,
        compaction_usage=RequestStats(),
    )


LIMIT_PATCH = "holmes.core.tool_calling_llm.compact_if_necessary"
COMPACTION_CHECK_PATCH = "holmes.core.tool_calling_llm.check_compaction_needed"
TOKEN_COUNT_PATCH = "holmes.core.tool_calling_llm.ToolCallingLLM._emit_token_count"


def _run_stream(tcl, msgs, provider):
    with patch(LIMIT_PATCH, side_effect=_passthrough_limiter), patch(
        COMPACTION_CHECK_PATCH, return_value=None
    ), patch(
        TOKEN_COUNT_PATCH,
        return_value=StreamMessage(event=StreamEvents.TOKEN_COUNT, data={}),
    ):
        return list(tcl.call_stream(msgs=msgs, pending_user_messages=provider))


def test_call_stream_appends_pending_messages_before_the_llm_call():
    tcl, llm = _tool_calling_llm([_llm_response(content="done")])
    provider = MagicMock(side_effect=[[{"role": "user", "content": "steer"}], [], []])
    events = _run_stream(tcl, [{"role": "user", "content": "q"}], provider)

    sent = llm.completion.call_args.kwargs["messages"]
    assert [m["content"] for m in sent] == ["q", "steer"]
    assert events[-1].event == StreamEvents.ANSWER_END
    assert [m["content"] for m in events[-1].data["messages"]][:2] == ["q", "steer"]


def test_call_stream_continues_when_a_message_arrives_as_the_model_finishes():
    tcl, llm = _tool_calling_llm(
        [_llm_response(content="first answer"), _llm_response(content="final answer")]
    )
    # Nothing before the first call; a message shows up exactly when the model
    # is about to finish; nothing afterwards.
    provider = MagicMock(
        side_effect=[[], [{"role": "user", "content": "wait, also X"}], [], []]
    )
    events = _run_stream(tcl, [{"role": "user", "content": "q"}], provider)

    assert llm.completion.call_count == 2
    second_call = llm.completion.call_args_list[1].kwargs["messages"]
    assert [m["content"] for m in second_call] == ["q", "first answer", "wait, also X"]
    kinds = [e.event for e in events]
    assert kinds.count(StreamEvents.ANSWER_END) == 1
    assert StreamEvents.AI_MESSAGE in kinds  # the first answer became intermediate
    assert events[-1].data["content"] == "final answer"
    assert events[-1].data["num_llm_calls"] == 2


def test_call_stream_without_provider_is_unchanged():
    tcl, llm = _tool_calling_llm([_llm_response(content="done")])
    events = _run_stream(tcl, [{"role": "user", "content": "q"}], None)
    assert llm.completion.call_count == 1
    assert events[-1].event == StreamEvents.ANSWER_END


# ---------------------------------------------------------------------------
# worker finalize loop
# ---------------------------------------------------------------------------


def _finish(w, terminal, followups, rounds=0, messages=None):
    cap = MagicMock()
    cap.messages = messages
    return w._finish_turn(
        _task(), "completed", terminal, followups, rounds, cap
    )


def _followups(enabled=True, consumed=4, new=None, fetch_failed=False):
    f = MagicMock(spec=MidTurnFollowups)
    f.enabled = enabled
    f.consumed_seq = consumed
    f.fetch_new.return_value = new or []
    f.last_fetch_failed = fetch_failed
    return f


def test_finish_turn_passes_consumed_seq_for_answers():
    w, _ = _bare_worker()
    assert _finish(w, StreamEvents.ANSWER_END, _followups()) is True
    assert w.dal.update_conversation_status.call_args.kwargs["consumed_seq"] == 4


def test_finish_turn_never_guards_an_approval_pause():
    w, _ = _bare_worker()
    _finish(w, StreamEvents.APPROVAL_REQUIRED, _followups())
    assert w.dal.update_conversation_status.call_args.kwargs["consumed_seq"] is None


def test_finish_turn_skips_guard_when_followups_disabled():
    w, _ = _bare_worker()
    _finish(w, StreamEvents.ANSWER_END, _followups(enabled=False))
    assert w.dal.update_conversation_status.call_args.kwargs["consumed_seq"] is None


def test_finish_turn_refused_with_new_messages_requests_another_round():
    w, _ = _bare_worker()
    w.dal.update_conversation_status.side_effect = PendingFollowupError("PENDING_FOLLOWUP")
    extra = [{"role": "user", "content": "steer"}]
    with pytest.raises(_CompletionRefused) as info:
        _finish(w, StreamEvents.ANSWER_END, _followups(new=extra), messages=[{"role": "user", "content": "q"}])
    assert info.value.extra == extra


def test_finish_turn_refused_but_nothing_to_feed_completes_unguarded():
    w, _ = _bare_worker()
    w.dal.update_conversation_status.side_effect = [PendingFollowupError("x"), True]
    assert _finish(w, StreamEvents.ANSWER_END, _followups(new=[]), messages=[{"role": "user", "content": "q"}]) is True
    second = w.dal.update_conversation_status.call_args_list[1].kwargs
    assert "consumed_seq" not in second


def test_finish_turn_defers_completion_when_the_followup_read_fails():
    """A PENDING_FOLLOWUP means an unread message exists; if the follow-up read
    then fails, an empty fetch is a failed read, not 'nothing to deliver', so the
    turn must not force an unguarded completion past the message."""
    w, _ = _bare_worker()
    w.dal.update_conversation_status.side_effect = PendingFollowupError("x")
    with pytest.raises(PendingFollowupError):
        _finish(
            w,
            StreamEvents.ANSWER_END,
            _followups(new=[], fetch_failed=True),
            messages=[{"role": "user", "content": "q"}],
        )
    # Only the guarded attempt ran — no unguarded completion was sent.
    assert w.dal.update_conversation_status.call_count == 1


def test_finish_turn_refused_past_the_round_limit_completes_unguarded():
    w, _ = _bare_worker()
    w.dal.update_conversation_status.side_effect = [PendingFollowupError("x"), True]
    extra = [{"role": "user", "content": "steer"}]
    assert (
        _finish(
            w,
            StreamEvents.ANSWER_END,
            _followups(new=extra),
            rounds=MAX_FOLLOWUP_CONTINUATIONS,
            messages=[{"role": "user", "content": "q"}],
        )
        is True
    )


def _drive(worker, ai, call_stream_results, update_side_effects, fetch_new_results):
    """Run _run_chat_and_publish with a scripted call_stream, a scripted DB
    verdict per round, and scripted follow-ups fetched after a refusal."""
    ai.call_stream = MagicMock(side_effect=call_stream_results)
    worker._inject_frontend_tools = MagicMock(return_value=ai)
    worker.dal.update_conversation_status = MagicMock(side_effect=update_side_effects)
    publisher = MagicMock()

    def consume(stream):
        list(stream)  # drain so TerminalMessagesCapture sees the terminal event
        return StreamEvents.ANSWER_END

    publisher.consume = MagicMock(side_effect=consume)

    fetches = iter(fetch_new_results)
    with patch(
        "holmes.core.conversations_worker.processor.stream_with_usage_recording",
        side_effect=lambda stream, _state: stream,
    ), patch(
        "holmes.core.conversations_worker.processor.build_chat_recorder_state"
    ), patch(
        "holmes.core.conversations_worker.processor.build_chat_messages",
        return_value=[{"role": "user", "content": "q"}],
    ), patch(
        "holmes.core.conversations_worker.processor.tool_result_storage"
    ) as storage, patch(
        "holmes.core.conversations_worker.processor.TracingFactory"
    ) as tracing, patch.object(
        MidTurnFollowups, "fetch_new", lambda self: next(fetches)
    ):
        storage.return_value.__enter__ = MagicMock(return_value="/tmp/x")
        storage.return_value.__exit__ = MagicMock(return_value=False)
        tracer = MagicMock()
        tracer.start_trace.return_value = MagicMock()
        tracing.create_tracer.return_value = tracer
        task = _task()
        task.consumed_seq = 1
        worker._run_chat_and_publish(
            task=task,
            chat_request=ChatRequest(ask="q", conversation_id="c1", conversation_source="conversations"),
            publisher=publisher,
        )
    return publisher


def _answer_stream(history):
    return iter([StreamMessage(event=StreamEvents.ANSWER_END, data={"messages": history})])


def test_run_chat_reruns_the_model_when_completion_is_refused():
    w, ai = _bare_worker()
    first_history = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a1"}]
    second_history = first_history + [{"role": "user", "content": "steer"}, {"role": "assistant", "content": "a2"}]
    publisher = _drive(
        w,
        ai,
        call_stream_results=[_answer_stream(first_history), _answer_stream(second_history)],
        update_side_effects=[PendingFollowupError("PENDING_FOLLOWUP"), True],
        fetch_new_results=[[{"role": "user", "content": "steer"}]],
    )

    assert ai.call_stream.call_count == 2
    second = ai.call_stream.call_args_list[1].kwargs
    assert second["msgs"] == first_history + [{"role": "user", "content": "steer"}]
    assert second["tool_decisions"] is None
    assert second["frontend_tool_results"] is None
    assert publisher.consume.call_count == 2
    assert w.dal.update_conversation_status.call_count == 2
    assert w.dal.update_conversation_status.call_args.kwargs["status"] == "completed"


def test_run_chat_registers_and_clears_the_followup_signal():
    w, ai = _bare_worker()
    seen = {}
    original = w._register_followup_signal

    def register(active_key):
        signal = original(active_key)
        seen["registered_key"] = active_key
        seen["present_during_run"] = active_key in w._followup_signals
        return signal

    w._register_followup_signal = register
    _drive(w, ai, call_stream_results=[_answer_stream([{"role": "user", "content": "q"}])], update_side_effects=[True], fetch_new_results=[])
    assert seen["registered_key"] == ("c1", 1)
    assert seen["present_during_run"] is True
    # Cleared in the finally block once the turn ends.
    assert ("c1", 1) not in w._followup_signals


def test_run_chat_passes_the_followup_provider_to_call_stream():
    w, ai = _bare_worker()
    _drive(w, ai, call_stream_results=[_answer_stream([{"role": "user", "content": "q"}])], update_side_effects=[True], fetch_new_results=[])
    provider = ai.call_stream.call_args.kwargs["pending_user_messages"]
    assert provider is not None
    assert provider.__self__.__class__ is MidTurnFollowups
