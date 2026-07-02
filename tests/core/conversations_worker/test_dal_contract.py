"""Unit tests for the SupabaseDal methods used by the M2 worker.

Verifies the RPC contract: parameter names, default values, and that the DAL
just forwards the response from the RPC.
"""
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from holmes.core.conversations_worker.models import ConversationReassignedError
from holmes.core.supabase_dal import SupabaseDal


def _build_dal(rpc_data: Any = None) -> SupabaseDal:
    """Build a DAL with a mocked supabase client whose rpc().execute() returns
    a result with the given data payload."""
    dal = SupabaseDal.__new__(SupabaseDal)
    dal.enabled = True
    dal.account_id = "acc-1"
    dal.cluster = "cluster-1"
    dal.client = MagicMock()
    dal.client.rpc = MagicMock()
    if rpc_data is not None:
        dal.client.rpc.return_value = MagicMock(
            execute=MagicMock(return_value=MagicMock(data=rpc_data))
        )
    return dal


# ---- post_conversation_events ----


def test_post_conversation_events_forwards_compact_flag():
    dal = _build_dal(rpc_data=7)
    dal.post_conversation_events(
        conversation_id="c",
        assignee="h",
        request_sequence=3,
        events=[{"event": "x", "data": {}, "ts": "t"}],
        compact=True,
    )
    dal.client.rpc.assert_called_once()
    args, _ = dal.client.rpc.call_args
    assert args[0] == "post_conversation_events"
    params = args[1]
    assert params["_account_id"] == "acc-1"
    assert params["_compact"] is True
    assert params["_conversation_id"] == "c"
    assert params["_assignee"] == "h"
    assert params["_request_sequence"] == 3


def test_post_conversation_events_default_compact_false():
    dal = _build_dal(rpc_data=1)
    dal.post_conversation_events(
        conversation_id="c",
        assignee="h",
        request_sequence=1,
        events=[{"event": "ai_message", "data": {}, "ts": "t"}],
    )
    params = dal.client.rpc.call_args[0][1]
    assert params["_compact"] is False


def test_post_conversation_events_retries_transient_error_then_succeeds():
    """A transient Supabase error should be retried and eventually succeed."""
    dal = _build_dal()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(
            side_effect=[
                Exception("502 Bad Gateway"),
                MagicMock(data=7),
            ]
        )
    )
    seq = dal.post_conversation_events(
        conversation_id="c",
        assignee="h",
        request_sequence=1,
        events=[{"event": "x", "data": {}, "ts": "t"}],
    )
    assert seq == 7
    assert dal.client.rpc.return_value.execute.call_count == 2


def test_post_conversation_events_raises_after_exhausting_retries():
    """Persistent transient errors exhaust retries and re-raise (caller handles)."""
    dal = _build_dal()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(side_effect=Exception("502 Bad Gateway"))
    )
    with pytest.raises(Exception, match="502 Bad Gateway"):
        dal.post_conversation_events(
            conversation_id="c",
            assignee="h",
            request_sequence=1,
            events=[{"event": "x", "data": {}, "ts": "t"}],
        )
    assert dal.client.rpc.return_value.execute.call_count == 3


def test_post_conversation_events_does_not_retry_mismatch():
    """MISMATCH errors must not be retried — the row was reassigned."""
    dal = _build_dal()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(
            side_effect=Exception("MISMATCH Assignee expected h-old, got h-new")
        )
    )
    with pytest.raises(ConversationReassignedError, match="MISMATCH"):
        dal.post_conversation_events(
            conversation_id="c",
            assignee="h",
            request_sequence=1,
            events=[{"event": "x", "data": {}, "ts": "t"}],
        )
    assert dal.client.rpc.return_value.execute.call_count == 1


# ---- get_conversation_events (RPC-based, returns flat list) ----


def test_get_conversation_events_calls_rpc_with_default_args():
    dal = _build_dal(rpc_data=[])
    dal.get_conversation_events(conversation_id="c")
    args, _ = dal.client.rpc.call_args
    assert args[0] == "get_conversation_events"
    params = args[1]
    assert params["_account_id"] == "acc-1"
    assert params["_conversation_id"] == "c"
    assert params["_include_compacted"] is False
    assert params["_min_seq"] == 1


def test_get_conversation_events_forwards_include_compacted():
    dal = _build_dal(rpc_data=[])
    dal.get_conversation_events(conversation_id="c", include_compacted=True)
    params = dal.client.rpc.call_args[0][1]
    assert params["_include_compacted"] is True


def test_get_conversation_events_forwards_min_seq():
    dal = _build_dal(rpc_data=[])
    dal.get_conversation_events(conversation_id="c", min_seq=42)
    params = dal.client.rpc.call_args[0][1]
    assert params["_min_seq"] == 42


def test_get_conversation_events_returns_flat_event_list():
    """RPC returns a flat list of event objects (not row-wrapped)."""
    flat_events = [
        {"event": "user_message", "data": {"ask": "hi"}, "ts": "1"},
        {"event": "ai_answer_end", "data": {"content": "hello"}, "ts": "2"},
    ]
    dal = _build_dal(rpc_data=flat_events)
    out = dal.get_conversation_events(conversation_id="c")
    assert out == flat_events


def test_get_conversation_events_returns_empty_list_when_disabled():
    dal = _build_dal()
    dal.enabled = False
    assert dal.get_conversation_events(conversation_id="c") == []
    dal.client.rpc.assert_not_called()


# ---- claim_n_pending_conversations ----


def test_claim_n_pending_conversations_forwards_limit():
    dal = _build_dal(rpc_data=[])
    dal.claim_n_pending_conversations(holmes_id="my-pod-1", limit=3)
    args, _ = dal.client.rpc.call_args
    assert args[0] == "claim_n_pending_conversations"
    params = args[1]
    assert params["_assignee"] == "my-pod-1"
    assert params["_account_id"] == "acc-1"
    assert params["_cluster_id"] == "cluster-1"
    assert params["_limit"] == 3


def test_claim_n_pending_conversations_skips_rpc_when_limit_not_positive():
    """A zero/negative limit means no free capacity — don't even hit the RPC."""
    dal = _build_dal(rpc_data=[])
    assert dal.claim_n_pending_conversations(holmes_id="h", limit=0) == []
    assert dal.claim_n_pending_conversations(holmes_id="h", limit=-1) == []
    dal.client.rpc.assert_not_called()


def test_claim_n_pending_conversations_retries_transient_error_then_succeeds():
    dal = _build_dal()
    claimed = [{"conversation_id": "c1"}]
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(
            side_effect=[
                Exception("502 Bad Gateway"),
                MagicMock(data=claimed),
            ]
        )
    )
    assert dal.claim_n_pending_conversations(holmes_id="h", limit=5) == claimed
    assert dal.client.rpc.return_value.execute.call_count == 2


def test_claim_n_pending_conversations_returns_empty_after_exhausting_retries():
    dal = _build_dal()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(side_effect=Exception("502 Bad Gateway"))
    )
    assert dal.claim_n_pending_conversations(holmes_id="h", limit=5) == []
    assert dal.client.rpc.return_value.execute.call_count == 3


# ---- claim_n_pending_tool_calls ----


def test_claim_n_pending_tool_calls_forwards_limit():
    dal = _build_dal(rpc_data=[])
    dal.claim_n_pending_tool_calls(holmes_id="my-pod-1", limit=4)
    args, _ = dal.client.rpc.call_args
    assert args[0] == "claim_n_pending_tool_calls"
    params = args[1]
    assert params["_assignee"] == "my-pod-1"
    assert params["_account_id"] == "acc-1"
    assert params["_cluster_id"] == "cluster-1"
    assert params["_limit"] == 4


def test_claim_n_pending_tool_calls_skips_rpc_when_limit_not_positive():
    dal = _build_dal(rpc_data=[])
    assert dal.claim_n_pending_tool_calls(holmes_id="h", limit=0) == []
    assert dal.claim_n_pending_tool_calls(holmes_id="h", limit=-1) == []
    dal.client.rpc.assert_not_called()


def test_claim_n_pending_tool_calls_retries_transient_error_then_succeeds():
    dal = _build_dal()
    claimed = [{"id": "t1"}]
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(
            side_effect=[
                Exception("502 Bad Gateway"),
                MagicMock(data=claimed),
            ]
        )
    )
    assert dal.claim_n_pending_tool_calls(holmes_id="h", limit=5) == claimed
    assert dal.client.rpc.return_value.execute.call_count == 2


def test_claim_n_pending_tool_calls_returns_empty_after_exhausting_retries():
    dal = _build_dal()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(side_effect=Exception("502 Bad Gateway"))
    )
    assert dal.claim_n_pending_tool_calls(holmes_id="h", limit=5) == []
    assert dal.client.rpc.return_value.execute.call_count == 3


# ---- update_conversation_status ----


def test_update_conversation_status_uses_assignee_param():
    dal = _build_dal(rpc_data=True)
    dal.update_conversation_status(
        conversation_id="c",
        request_sequence=2,
        assignee="my-pod-1",
        status="completed",
    )
    args, _ = dal.client.rpc.call_args
    assert args[0] == "update_conversation_status"
    params = args[1]
    assert params["_account_id"] == "acc-1"
    assert params["_conversation_id"] == "c"
    assert params["_request_sequence"] == 2
    assert params["_assignee"] == "my-pod-1"
    assert params["_status"] == "completed"


def test_update_conversation_status_accepts_running():
    dal = _build_dal(rpc_data=True)
    result = dal.update_conversation_status(
        conversation_id="c",
        request_sequence=1,
        assignee="h",
        status="running",
    )
    assert result is True
    params = dal.client.rpc.call_args[0][1]
    assert params["_status"] == "running"


def test_update_conversation_status_accepts_queued():
    dal = _build_dal(rpc_data=True)
    result = dal.update_conversation_status(
        conversation_id="c",
        request_sequence=1,
        assignee="h",
        status="queued",
    )
    assert result is True
    params = dal.client.rpc.call_args[0][1]
    assert params["_status"] == "queued"


def test_update_conversation_status_rejects_invalid_status():
    dal = _build_dal(rpc_data=True)
    result = dal.update_conversation_status(
        conversation_id="c",
        request_sequence=1,
        assignee="h",
        status="stopped",
    )
    assert result is False
    dal.client.rpc.assert_not_called()


def test_update_conversation_status_promotes_mismatch_to_reassigned_error():
    """MISMATCH errors from the RPC should be raised as ConversationReassignedError."""
    dal = SupabaseDal.__new__(SupabaseDal)
    dal.enabled = True
    dal.account_id = "acc-1"
    dal.cluster = "cluster-1"
    dal.client = MagicMock()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(
            side_effect=Exception("MISMATCH Assignee expected h-old, got h-new")
        )
    )
    with pytest.raises(ConversationReassignedError, match="MISMATCH"):
        dal.update_conversation_status(
            conversation_id="c",
            request_sequence=1,
            assignee="h",
            status="running",
        )


def test_update_conversation_status_retries_transient_error_then_succeeds():
    """A transient Supabase error should be retried and eventually succeed."""
    dal = _build_dal()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(
            side_effect=[
                Exception("502 Bad Gateway"),
                MagicMock(data=True),
            ]
        )
    )
    result = dal.update_conversation_status(
        conversation_id="c",
        request_sequence=1,
        assignee="h",
        status="completed",
    )
    assert result is True
    assert dal.client.rpc.return_value.execute.call_count == 2


def test_update_conversation_status_returns_false_after_exhausting_retries():
    """Persistent transient errors exhaust retries and return False (not raise)."""
    dal = _build_dal()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(side_effect=Exception("502 Bad Gateway"))
    )
    result = dal.update_conversation_status(
        conversation_id="c",
        request_sequence=1,
        assignee="h",
        status="completed",
    )
    assert result is False
    assert dal.client.rpc.return_value.execute.call_count == 3


def test_update_conversation_status_does_not_retry_mismatch():
    """MISMATCH errors must not be retried — the row was reassigned."""
    dal = _build_dal()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(
            side_effect=Exception("MISMATCH Assignee expected h-old, got h-new")
        )
    )
    with pytest.raises(ConversationReassignedError, match="MISMATCH"):
        dal.update_conversation_status(
            conversation_id="c",
            request_sequence=1,
            assignee="h",
            status="running",
        )
    assert dal.client.rpc.return_value.execute.call_count == 1


# ---- post_remote_tool_call_result (mirrors update_conversation_status retry) ----


def _post_result(dal):
    return dal.post_remote_tool_call_result(
        tool_call_id="tc-1",
        assignee="h",
        status="completed",
        tool_response={"status": "SUCCESS", "data": "ok"},
    )


def test_post_remote_tool_call_result_retries_transient_error_then_succeeds():
    """A transient Supabase error should be retried and eventually succeed."""
    dal = _build_dal()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(
            side_effect=[
                Exception("502 Bad Gateway"),
                MagicMock(data=True),
            ]
        )
    )
    assert _post_result(dal) is True
    assert dal.client.rpc.return_value.execute.call_count == 2


def test_post_remote_tool_call_result_returns_false_after_exhausting_retries():
    """Persistent transient errors exhaust retries and return False (not raise)."""
    dal = _build_dal()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(side_effect=Exception("502 Bad Gateway"))
    )
    assert _post_result(dal) is False
    assert dal.client.rpc.return_value.execute.call_count == 3


def test_post_remote_tool_call_result_does_not_retry_mismatch():
    """MISMATCH / not-found means the row was reassigned/terminal — drop it
    (return False) without retrying (first result wins)."""
    dal = _build_dal()
    dal.client.rpc.return_value = MagicMock(
        execute=MagicMock(
            side_effect=Exception("MISMATCH Assignee expected h-old, got h-new")
        )
    )
    assert _post_result(dal) is False
    assert dal.client.rpc.return_value.execute.call_count == 1
