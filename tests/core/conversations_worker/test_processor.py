"""ConversationProcessor: the outcome writers and run()'s error handling."""

from unittest.mock import MagicMock, patch

from holmes.core.conversations_worker.models import (
    ConversationReassignedError,
    ConversationTask,
)
from holmes.core.conversations_worker.models import (
    SHUTDOWN_ERROR_CODE,
    SHUTDOWN_REASON,
)
from holmes.core.conversations_worker.processor import ConversationProcessor


def _processor():
    dal = MagicMock()
    dal.update_conversation_status = MagicMock(return_value=True)
    return ConversationProcessor(dal=dal, config=MagicMock(), holmes_id="h-test")


def _task(cid="c1", seq=1):
    return ConversationTask(
        conversation_id=cid,
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=seq,
    )


def test_run_marks_failed_on_exception_without_leaking_the_message():
    p = _processor()

    def boom(*a, **kw):
        raise RuntimeError("synthetic failure")

    with patch.object(ConversationProcessor, "_process_conversation", boom):
        p.run(_task())

    p.dal.post_conversation_events.assert_called_once()
    kwargs = p.dal.post_conversation_events.call_args.kwargs
    assert kwargs["conversation_id"] == "c1"
    desc = kwargs["events"][0]["data"]["description"]
    assert "synthetic failure" not in desc
    assert "internal error" in desc.lower()
    p.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1", request_sequence=1, assignee="h-test", status="failed"
    )


def test_run_writes_nothing_on_reassignment():
    p = _processor()

    def boom(*a, **kw):
        raise ConversationReassignedError("x")

    with patch.object(ConversationProcessor, "_process_conversation", boom):
        p.run(_task())
    p.dal.update_conversation_status.assert_not_called()
    p.dal.post_conversation_events.assert_not_called()


def test_fail_posts_error_event_then_marks_failed():
    p = _processor()
    p.fail(_task(), "why", error_code=4321, raw_error="raw")
    kwargs = p.dal.post_conversation_events.call_args.kwargs
    (event,) = kwargs["events"]
    assert event["event"] == "error"
    assert event["data"]["description"] == "why" and event["data"]["msg"] == "why"
    assert event["data"]["error_code"] == 4321
    assert event["data"]["raw_error"] == "raw"
    assert "reason" not in event["data"]
    p.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1", request_sequence=1, assignee="h-test", status="failed"
    )


def test_fail_survives_dal_errors():
    p = _processor()
    p.dal.post_conversation_events.side_effect = RuntimeError("down")
    p.dal.update_conversation_status.side_effect = RuntimeError("down")
    p.fail(_task(), "why")  # must not raise


def test_fail_unparsed_row_closes_out_an_unparseable_claimed_row():
    p = _processor()
    # No account_id / cluster_id: ConversationTask.from_row would refuse this row.
    p.fail_unparsed_row({"conversation_id": "c9", "request_sequence": 2}, "bad row")
    p.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c9", request_sequence=2, assignee="h-test", status="failed"
    )
    assert p.dal.post_conversation_events.call_args.kwargs["request_sequence"] == 2


def test_fail_unparsed_row_ignores_rows_without_an_identity():
    p = _processor()
    p.fail_unparsed_row({"request_sequence": 1}, "bad row")
    p.fail_unparsed_row({"conversation_id": "c9", "request_sequence": "x"}, "bad row")
    p.dal.update_conversation_status.assert_not_called()


def test_mark_timed_out_writes_timeout_only():
    p = _processor()
    p.dal.update_conversation_status = MagicMock(return_value=False)
    p.mark_timed_out(_task())
    statuses = [
        c.kwargs["status"] for c in p.dal.update_conversation_status.call_args_list
    ]
    assert statuses == ["timeout"]


def test_mark_timed_out_stops_when_row_was_reassigned():
    p = _processor()
    p.dal.update_conversation_status = MagicMock(
        side_effect=ConversationReassignedError("MISMATCH")
    )
    p.mark_timed_out(_task())
    assert p.dal.update_conversation_status.call_count == 1


def test_retire_posts_the_restart_reason_then_times_out():
    p = _processor()
    p.retire(_task("c1", 2))
    kwargs = p.dal.post_conversation_events.call_args.kwargs
    assert kwargs["conversation_id"] == "c1" and kwargs["request_sequence"] == 2
    (event,) = kwargs["events"]
    assert event["data"]["reason"] == SHUTDOWN_REASON
    assert SHUTDOWN_REASON in event["data"]["description"]
    assert event["data"]["error_code"] == SHUTDOWN_ERROR_CODE
    p.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1", request_sequence=2, assignee="h-test", status="timeout"
    )


def test_retire_never_raises():
    p = _processor()
    p.dal.post_conversation_events.side_effect = RuntimeError("boom")
    p.dal.update_conversation_status.side_effect = RuntimeError("boom")
    p.retire(_task())
