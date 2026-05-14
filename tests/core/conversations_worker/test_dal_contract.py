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


# ---- claim_conversations ----


def test_claim_conversations_uses_assignee_param():
    dal = _build_dal(rpc_data=[])
    dal.claim_conversations(holmes_id="my-pod-1")
    args, _ = dal.client.rpc.call_args
    assert args[0] == "claim_conversations"
    params = args[1]
    assert params["_assignee"] == "my-pod-1"
    assert params["_account_id"] == "acc-1"
    assert params["_cluster_id"] == "cluster-1"


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
