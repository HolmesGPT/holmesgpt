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
    w._saturated_since = None
    w._saturation_logged = False
    w._last_stuck_warn = None
    w._executor = MagicMock()
    w._active_conversation_ids = {}
    w._active_lock = threading.Lock()
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


def _run(worker, ai, task=None, chat_request=None, consume_side_effect=None):
    """Drive _run_chat_and_publish with all heavy collaborators mocked.

    Returns the captured (raw_stream, recorder_state, wrapped_stream) so
    individual tests can assert on each. `consume_side_effect` is what the
    publisher raises while consuming the stream, for the failure branches.
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
    publisher.consume = MagicMock(
        return_value=StreamEvents.ANSWER_END, side_effect=consume_side_effect
    )

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
            task=task or _task(),
            chat_request=chat_request or _chat_request(),
            publisher=publisher,
        )
        captured["call_stream_call"] = ai.call_stream.call_args

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
# user_id comes from the Conversations row; request_source falls back to
# Conversations.metadata.
#
# The FE writes user_id (column) and request_source (under metadata) onto
# the Conversations row when it creates a chat and does not repeat them in
# per-turn user_message events. user_id is an authorization key (OAuth,
# personal skills, relay RBAC) and the row is RLS-bound to the creator, so
# it is the ONLY source -- an event user_id that disagrees fails the turn
# (ROB-1107). request_source is an attribution hint and keeps the
# event-then-row fallback. These tests pin both so a refactor can't
# reintroduce the NULL-row bug or the event-first precedence.
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


def _process_and_capture(task, user_message_data):
    """Like _capture_chat_request_from_process but also returns the worker so
    tests can assert on the failure path (no ChatRequest, error event)."""
    worker, _ = _bare_worker()
    worker.dal.get_conversation_events = MagicMock(
        return_value=[{"event": "user_message", "data": user_message_data, "ts": "1"}]
    )
    captured = {}

    def capture(self, t, chat_request, publisher, resume_only=False):
        captured["chat_request"] = chat_request

    with patch.object(ConversationWorker, "_run_chat_and_publish", capture), \
            patch.object(ConversationWorker, "_fail_conversation") as fail:
        worker._process_conversation(task)

    return captured.get("chat_request"), fail


def _task(user_id="u-conversations-row"):
    return ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
        user_id=user_id,
    )


def test_event_user_id_mismatch_rejects_turn():
    # ROB-1107: the user_message event's data is a client-controlled blob.
    # A user_id there that differs from the RLS-bound Conversations row
    # owner must never be acted on (OAuth tokens, personal skills, relay
    # RBAC header, usage attribution all key on ChatRequest.user_id). The
    # turn fails instead of running under either identity.
    cr, fail = _process_and_capture(_task(), {"ask": "q", "user_id": "u-victim"})
    assert cr is None, "ChatRequest must not be built for a spoofed identity"
    fail.assert_called_once()
    assert fail.call_args.args[0].conversation_id == "c1"
    assert "owner" in fail.call_args.args[1]


def test_event_user_id_mismatch_rejected_when_row_has_no_owner():
    # Shared / automated conversations (user_id NULL on the row, e.g.
    # triggered workflows) must not be upgraded to a named user by whoever
    # posts the follow-up.
    cr, fail = _process_and_capture(_task(user_id=None), {"ask": "q", "user_id": "u-victim"})
    assert cr is None
    fail.assert_called_once()


def test_event_user_id_matching_row_is_accepted():
    # Redundant but consistent user_id in the event is harmless.
    cr, fail = _process_and_capture(
        _task(), {"ask": "q", "user_id": "u-conversations-row"}
    )
    fail.assert_not_called()
    assert cr is not None
    assert cr.user_id == "u-conversations-row"


def test_event_user_id_never_reaches_chat_request():
    # Even when accepted (matching), ChatRequest.user_id comes from the row,
    # not from the event -- pin the source, not just the value.
    task = _task()
    cr, _ = _process_and_capture(task, {"ask": "q", "user_id": task.user_id})
    assert cr is not None
    assert cr.user_id is task.user_id or cr.user_id == task.user_id


def test_empty_event_user_id_is_ignored():
    cr, fail = _process_and_capture(_task(), {"ask": "q", "user_id": ""})
    fail.assert_not_called()
    assert cr is not None and cr.user_id == "u-conversations-row"


def test_no_owner_and_no_event_user_id_runs_unattributed():
    cr, fail = _process_and_capture(_task(user_id=None), {"ask": "q"})
    fail.assert_not_called()
    assert cr is not None and cr.user_id is None


def test_spoofed_user_id_on_tool_decision_resume_is_rejected():
    worker, _ = _bare_worker()
    worker.dal.get_conversation_events = MagicMock(return_value=[
        {"event": "user_message", "data": {"ask": "first"}, "ts": "1"},
        {"event": "approval_required", "ts": "2", "data": {"messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "t1"}]},
        ]}},
        {"event": "user_message", "ts": "3", "data": {
            "tool_decisions": [{"tool_call_id": "t1", "approved": True}],
            "user_id": "u-victim",
        }},
    ])
    with patch.object(ConversationWorker, "_run_chat_and_publish") as run, \
            patch.object(ConversationWorker, "_fail_conversation") as fail:
        worker._process_conversation(_task())
    run.assert_not_called()
    fail.assert_called_once()


def test_mismatch_posts_error_event_and_marks_failed():
    worker, _ = _bare_worker()
    worker.dal.get_conversation_events = MagicMock(
        return_value=[{"event": "user_message", "data": {"ask": "q", "user_id": "u-victim"}, "ts": "1"}]
    )
    with patch.object(ConversationWorker, "_run_chat_and_publish") as run:
        worker._process_conversation(_task())
    run.assert_not_called()
    posted = worker.dal.post_conversation_events.call_args
    assert posted is not None
    events = posted.kwargs.get("events") or posted.args[-1]
    assert any(e.get("event") == "error" and "owner" in e["data"]["description"] for e in events)
    status_call = worker.dal.update_conversation_status.call_args
    assert status_call.kwargs.get("status") == "failed"


def test_metadata_oauth_enabled_false_drops_user_id():
    task = _task()
    task.metadata = {"oauth_enabled": False}
    cr, fail = _process_and_capture(task, {"ask": "q"})
    fail.assert_not_called()
    assert cr is not None and cr.user_id is None


def test_request_context_carries_owner_when_oauth_opt_out_drops_user_id():
    worker, ai = _bare_worker()
    task = ConversationTask(
        conversation_id="c1", account_id="a1", cluster_id="cl1", origin="chat",
        request_sequence=1, user_id="u-owner", metadata={"oauth_enabled": False},
    )
    cr = _chat_request()
    cr.user_id = None
    captured = _run(worker, ai, task=task, chat_request=cr)
    ctx = captured["call_stream_call"].kwargs["request_context"]
    assert ctx["conversation_owner_id"] == "u-owner"
    assert "user_id" not in ctx


def test_request_context_carries_owner_and_user_id_normally():
    worker, ai = _bare_worker()
    task = ConversationTask(
        conversation_id="c1", account_id="a1", cluster_id="cl1", origin="chat",
        request_sequence=1, user_id="u-1",
    )
    captured = _run(worker, ai, task=task)
    ctx = captured["call_stream_call"].kwargs["request_context"]
    assert ctx["user_id"] == "u-1" and ctx["conversation_owner_id"] == "u-1"


def test_request_context_has_no_owner_for_ownerless_row():
    worker, ai = _bare_worker()
    cr = _chat_request()
    cr.user_id = None
    captured = _run(worker, ai, task=_task(user_id=None), chat_request=cr)
    ctx = captured["call_stream_call"].kwargs["request_context"]
    assert "conversation_owner_id" not in ctx and "user_id" not in ctx


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


def test_conversation_link_read_from_conversations_metadata():
    # conversation_link is conversation-level: the surface a chat originated
    # from (Slack thread, Teams message, workflow run) does not change between
    # turns, so relay stamps it once on the Conversations row's metadata.
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
        metadata={"conversation_link": "https://acme.slack.com/archives/C1/p123"},
    )
    cr = _capture_chat_request_from_process(task, {"ask": "follow-up?"})
    assert cr is not None
    assert cr.conversation_link == "https://acme.slack.com/archives/C1/p123"


def test_freeform_chat_conversation_link_is_server_derived(monkeypatch):
    # For freeform platform chats the worker derives the link itself from the
    # task's own ids — a client-writable metadata value must not pick the
    # destination.
    monkeypatch.setattr(
        "holmes.core.conversation_links.ROBUSTA_UI_DOMAIN",
        "https://platform.robusta.dev",
    )
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
        metadata={
            "request_source": "freeform",
            "conversation_link": "https://evil.example/spoof",
        },
    )
    cr = _capture_chat_request_from_process(task, {"ask": "q"})
    assert cr is not None
    assert cr.conversation_link == (
        "https://platform.robusta.dev/holmes/chat/c1?account_id=a1"
    )


def test_freeform_chat_gets_no_link_when_derivation_fails(monkeypatch):
    # A freeform chat's link is server-derived or nothing: when derivation
    # can't run (here: no UI domain configured), the client-writable metadata
    # value must not slip in as a fallback.
    monkeypatch.setattr("holmes.core.conversation_links.ROBUSTA_UI_DOMAIN", "")
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
        metadata={
            "request_source": "freeform",
            "conversation_link": "https://acme.slack.com/archives/C1/p123",
        },
    )
    cr = _capture_chat_request_from_process(task, {"ask": "q"})
    assert cr is not None
    assert cr.conversation_link is None


def test_event_conversation_link_is_ignored():
    # Only relay legitimately sets this key, and it stamps metadata — a
    # per-turn event value is a client-side override attempt and is ignored.
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
        metadata={"conversation_link": "https://acme.slack.com/archives/C1/p123"},
    )
    cr = _capture_chat_request_from_process(
        task, {"ask": "q", "conversation_link": "https://evil.example/override"}
    )
    assert cr is not None
    assert cr.conversation_link == "https://acme.slack.com/archives/C1/p123"


def test_a_relay_refusal_fails_the_conversation_with_its_own_code():
    """The platform refusing the call on a Robusta-hosted model surfaces while
    the stream is consumed. The error event carries relay's sentence and the
    refusal's code, so the client can tell it from a crash (ROB-1389)."""
    from holmes.core.relay_refusal import RELAY_REFUSAL_ERROR_CODES, RelayRefusal

    w, ai = _bare_worker()
    ai.llm.is_robusta_model = True
    message = "Robusta-hosted models are disabled for this account."

    _run(w, ai, consume_side_effect=RelayRefusal(message, 403))

    w.dal.post_conversation_events.assert_called_once()
    event = w.dal.post_conversation_events.call_args[1]["events"][0]
    assert event["event"] == "error"
    assert event["data"]["error_code"] == RELAY_REFUSAL_ERROR_CODES[403]
    assert event["data"]["description"] == message
    assert event["data"]["raw_error"] == message
    w.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1", request_sequence=1, assignee="h-test", status="failed"
    )
