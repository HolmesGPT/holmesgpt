"""Verify the ConversationWorker wires the usage recorder around the LLM
stream so worker-driven chats produce HolmesUsageEvents rows.

Context: the worker takes a code path that bypasses server.py::chat() and
calls request_ai.call_stream(...) directly. Before this wiring, that path
never invoked the recorder, so worker-driven conversations (the new
/api/conversations flow) were silently missing from HolmesUsageEvents
while only server.py-direct calls were tracked.

These tests assert the integration without re-testing the recorder itself
(which is covered in tests/core/test_usage_recorder.py):
1. ``stream_with_usage_recording`` is invoked with the raw stream.
2. The wrapped stream — not the raw stream — is what ``publisher.consume``
   receives, so the recorder gets a chance to observe terminal events.
3. The recorder state passed in carries the worker's classification
   (``conversation_source='conversations'``, ``request_type='user_chat'``,
   etc.) so dashboards can attribute these rows correctly.
"""
import threading
from collections import deque
from unittest.mock import MagicMock, patch

from holmes.core.conversations_worker.models import ConversationTask
from holmes.core.conversations_worker.worker import ConversationWorker
from holmes.core.models import ChatRequest


def _bare_worker():
    w = ConversationWorker.__new__(ConversationWorker)
    w.dal = MagicMock()
    w.dal.enabled = True
    w.dal.update_conversation_status = MagicMock(return_value=True)
    w.dal.get_global_instructions_for_account = MagicMock(return_value=None)
    w.config = MagicMock()
    # create_toolcalling_llm returns the AI; we configure its llm attrs so
    # build_chat_recorder_state can read model / is_robusta_model.
    ai = MagicMock()
    ai.llm = MagicMock()
    ai.llm.model = "anthropic/claude-sonnet-4-5"
    ai.llm.is_robusta_model = False
    w.config.create_toolcalling_llm = MagicMock(return_value=ai)
    w.config.get_skill_catalog = MagicMock(return_value=[])
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
    return w, ai


def _task():
    return ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )


def _chat_request():
    return ChatRequest(
        ask="why is my pod failing?",
        stream=True,
        request_type="user_chat",
        conversation_id="c1",
        conversation_source="conversations",  # worker sets this explicitly
        user_id="u-1",
    )


def _run(worker, ai):
    """Drive _run_chat_and_publish with all heavy collaborators mocked.

    Returns the captured (raw_stream, recorder_state, wrapped_stream) so
    individual tests can assert on each.
    """
    raw_stream = iter(["raw-event-1", "raw-event-2"])
    wrapped_stream_sentinel = object()

    ai.call_stream = MagicMock(return_value=raw_stream)
    # _inject_frontend_tools just returns the AI back when there are no
    # frontend tools; bypass it so we don't need to mock the helper module.
    worker._inject_frontend_tools = MagicMock(return_value=ai)

    publisher = MagicMock()
    # consume returns ANSWER_END so the worker doesn't take the failed-conversation
    # branch and try to call _fail_conversation.
    from holmes.utils.stream import StreamEvents
    publisher.consume = MagicMock(return_value=StreamEvents.ANSWER_END)

    captured = {}
    with patch(
        "holmes.core.conversations_worker.worker.stream_with_usage_recording"
    ) as mock_wrap, patch(
        "holmes.core.conversations_worker.worker.build_chat_recorder_state"
    ) as mock_build_state, patch(
        "holmes.core.conversations_worker.worker.build_chat_messages"
    ) as mock_build_messages, patch(
        "holmes.core.conversations_worker.worker.tool_result_storage"
    ) as mock_storage, patch(
        "holmes.core.conversations_worker.worker.TracingFactory"
    ) as mock_tracing:
        # build_chat_messages is heavy (Jinja, prompts) — return a fake list.
        mock_build_messages.return_value = [{"role": "user", "content": "fake"}]
        # tool_result_storage is a context manager.
        mock_storage.return_value.__enter__ = MagicMock(return_value="/tmp/x")
        mock_storage.return_value.__exit__ = MagicMock(return_value=False)
        # Tracing returns a tracer that returns a span with .log/.end.
        tracer = MagicMock()
        span = MagicMock()
        tracer.start_trace.return_value = span
        mock_tracing.create_tracer.return_value = tracer

        recorder_state_sentinel = MagicMock(name="recorder_state")
        mock_build_state.return_value = recorder_state_sentinel
        mock_wrap.return_value = wrapped_stream_sentinel

        worker._run_chat_and_publish(
            task=_task(),
            chat_request=_chat_request(),
            publisher=publisher,
        )

        captured["raw_stream"] = raw_stream
        captured["wrap_call"] = mock_wrap.call_args
        captured["build_state_call"] = mock_build_state.call_args
        captured["wrapped_stream"] = wrapped_stream_sentinel
        captured["recorder_state"] = recorder_state_sentinel
        captured["publisher"] = publisher

    return captured


def test_stream_is_wrapped_with_usage_recorder():
    """The recorder wrapper must see the raw stream so it can observe
    TOOL_RESULT / ANSWER_END events as they flow past."""
    worker, ai = _bare_worker()
    captured = _run(worker, ai)

    wrap_call = captured["wrap_call"]
    assert wrap_call is not None, (
        "stream_with_usage_recording was never called — the worker is "
        "still bypassing the recorder."
    )
    # First positional arg is the raw stream.
    assert wrap_call.args[0] is captured["raw_stream"]
    # Second positional arg is the recorder state.
    assert wrap_call.args[1] is captured["recorder_state"]


def test_publisher_consumes_wrapped_stream_not_raw():
    """If the publisher consumed the raw stream directly, the recorder's
    finally-block would never see the terminal event and would mark the row
    'aborted'. The wrapped stream must be the one passed to the publisher."""
    worker, ai = _bare_worker()
    captured = _run(worker, ai)

    consume_args, _ = captured["publisher"].consume.call_args
    assert consume_args[0] is captured["wrapped_stream"], (
        "publisher.consume() must receive the wrapped stream, not the raw one. "
        f"Got {consume_args[0]!r}"
    )


def test_recorder_state_uses_workers_dal_and_streaming_flag():
    """build_chat_recorder_state must be called with the worker's dal and
    is_streaming=True (worker is always streaming). Without this, telemetry
    would either fall on the floor (no dal) or be misclassified as
    non-streaming."""
    worker, ai = _bare_worker()
    captured = _run(worker, ai)

    build_call = captured["build_state_call"]
    assert build_call.kwargs.get("dal") is worker.dal
    assert build_call.kwargs.get("is_streaming") is True
    # Positional args are (chat_request, request_ai).
    assert build_call.args[1] is ai


# --------------------------------------------------------------------------
# user_id / request_source fallback into Conversations row + metadata.
#
# The FE writes user_id (column) and request_source (under metadata) onto
# the Conversations row when it creates a chat. It does NOT necessarily
# repeat them in every per-turn user_message event's data. Without these
# fallbacks, follow-up turns produce HolmesUsageEvents rows with NULL
# user_id and request_source even though the values are sitting on the
# Conversations row the worker already loaded. These tests pin the
# fallback behavior so a future refactor can't silently re-introduce the
# NULL-row bug the user reported.
# --------------------------------------------------------------------------

def _capture_chat_request_from_process(task, user_message_data):
    """Drive _process_conversation just far enough to capture the
    ChatRequest it constructs. Patches _run_chat_and_publish so the
    LLM pipeline never runs."""
    worker, _ = _bare_worker()
    worker.dal.get_conversation_events = MagicMock(
        return_value=[{"event": "user_message", "data": user_message_data, "ts": "1"}]
    )

    captured = {}

    def capture(self, t, chat_request, publisher, resume_only=False):
        captured["chat_request"] = chat_request

    with patch.object(ConversationWorker, "_run_chat_and_publish", capture):
        worker._process_conversation(task)

    return captured.get("chat_request")


def test_user_id_falls_back_to_conversations_row_when_event_omits_it():
    # FE wrote user_id onto the Conversations row but didn't repeat it in
    # the per-turn user_message data — exactly the symptom the user
    # reported (HolmesUsageEvents.user_id NULL despite the value being
    # known to the worker).
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
        user_id="u-conversations-row",
    )
    cr = _capture_chat_request_from_process(task, {"ask": "follow-up?"})
    assert cr is not None
    assert cr.user_id == "u-conversations-row"


def test_event_user_id_wins_over_conversations_row():
    # If the FE DOES repeat user_id in the event data, that value wins —
    # so a future flow that lets users hand-off a chat could still record
    # the per-turn rater. The Conversations-row value is a fallback, not
    # an override.
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
        user_id="u-conversations-row",
    )
    cr = _capture_chat_request_from_process(
        task, {"ask": "q", "user_id": "u-from-event"}
    )
    assert cr is not None
    assert cr.user_id == "u-from-event"


def test_request_source_falls_back_to_conversations_metadata():
    # FE puts request_source under Conversations.metadata when it creates
    # the row. Per-turn events typically don't repeat it. Worker should
    # pull from metadata so dashboards can slice by request_source even
    # for follow-up turns.
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
        metadata={"request_source": "alert_investigation", "other": "x"},
    )
    cr = _capture_chat_request_from_process(task, {"ask": "follow-up?"})
    assert cr is not None
    assert cr.request_source == "alert_investigation"


def test_event_request_source_wins_over_conversations_metadata():
    # Same caller-wins semantic as user_id. The conversation may have been
    # created from one surface ('alert_investigation') but a follow-up
    # turn could legitimately re-classify itself ('freeform').
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
        metadata={"request_source": "alert_investigation"},
    )
    cr = _capture_chat_request_from_process(
        task, {"ask": "q", "request_source": "freeform"}
    )
    assert cr is not None
    assert cr.request_source == "freeform"


def test_no_fallback_values_anywhere_yields_null():
    # Defense: if neither the row nor the event carries either field, the
    # ChatRequest must still build (just with NULLs that the recorder
    # writes through to the row).
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    cr = _capture_chat_request_from_process(task, {"ask": "q"})
    assert cr is not None
    assert cr.user_id is None
    assert cr.request_source is None


# --------------------------------------------------------------------------
# request_type passthrough.
#
# The worker used to hard-code request_type='user_chat' on the ChatRequest
# it constructed. That defeated build_chat_recorder_state's auto-detection
# logic: the helper only auto-classifies (Slack-prefix etc.) when
# chat_request.request_type is falsy, so a hard-coded value short-circuited
# every detection path. These tests pin the passthrough behavior so future
# refactors can't silently re-introduce that bug.
# --------------------------------------------------------------------------


def test_request_type_passes_through_from_event_data():
    # FE-supplied request_type wins. Today only /api/chat sees this, but
    # the runner could write request_type into the user_message blob at
    # any time without code changes here.
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    cr = _capture_chat_request_from_process(
        task, {"ask": "q", "request_type": "scheduled_prompt"}
    )
    assert cr is not None
    assert cr.request_type == "scheduled_prompt"


def test_request_type_unset_when_event_omits_it():
    # Critical: when the event doesn't supply request_type, the worker
    # MUST leave it None on the ChatRequest so build_chat_recorder_state's
    # auto-detection (Slack prefix, fallback default) gets to run. If the
    # worker hard-codes 'user_chat' here, Slack rows get mis-tagged.
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    cr = _capture_chat_request_from_process(task, {"ask": "q"})
    assert cr is not None
    assert cr.request_type is None, (
        "Worker must leave request_type=None so build_chat_recorder_state "
        "can auto-detect (e.g. Slack prefix → 'slack_chat'). Hard-coding "
        "'user_chat' defeats the helper's detection path."
    )


def test_slack_prefix_in_event_ask_routes_to_slack_chat_via_helper():
    # End-to-end check: an ask carrying the runner's Slack prefix must
    # arrive at build_chat_recorder_state with request_type=None so the
    # helper tags it as 'slack_chat'. We don't call the helper here —
    # that's covered in tests/test_chat_recorder_state.py — we only
    # assert the worker passes the right inputs into it.
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    slack_ask = (
        "**@user_U0AKMP2CZ97** • 2026-05-04T05:10:04Z\n\n"
        "high cpu in pod alert"
    )
    cr = _capture_chat_request_from_process(task, {"ask": slack_ask})
    assert cr is not None
    # ChatRequest.ask carries the original prefix (helper inspects this).
    assert cr.ask.startswith("**@user_U0AKMP2CZ97**")
    # And request_type is None so the helper's auto-detect runs.
    assert cr.request_type is None
