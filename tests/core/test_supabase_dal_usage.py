"""Unit tests for SupabaseDal.record_usage_event.

Feedback writes are not handled by Holmes (the FE calls the
public.record_feedback() Postgres RPC directly via supabase.rpc), so there
are no Holmes-side feedback unit tests here — coverage lives in the
migration's integration verification.

record_usage_event is best-effort: it swallows Supabase errors so the
response path can never be broken by a telemetry write. The tests verify
both the happy path (correct payload sent to .insert) and the failure path
(exceptions are absorbed).

The DAL takes a ``UsageRecorderState`` positional arg (Moshe's review on
PR #1969 — single-object signature avoids drift between the recorder and
DAL when fields are added). Tests construct a real UsageRecorderState via
the ``_make_state`` helper below; mock_dal is duck-typed so any object
with the same attributes would also work.
"""

from unittest.mock import MagicMock, patch

import pytest

from holmes.core.llm_usage import RequestStats
from holmes.core.supabase_dal import (
    HOLMES_USAGE_EVENTS_TABLE,
    SupabaseDal,
)
from holmes.core.usage_recorder import UsageRecorderState


@pytest.fixture
def mock_dal():
    """A SupabaseDal with mocked Supabase client and account_id."""
    with patch("holmes.core.supabase_dal.create_client"):
        dal = SupabaseDal(cluster="test-cluster")
        dal.enabled = True
        dal.account_id = "00000000-0000-0000-0000-000000000001"
        dal.cluster = "test-cluster"
        dal.client = MagicMock()
        return dal


def _stats() -> RequestStats:
    return RequestStats(
        total_cost=0.0123,
        total_tokens=1234,
        prompt_tokens=1000,
        completion_tokens=234,
        cached_tokens=50,
        reasoning_tokens=12,
        max_completion_tokens_per_call=234,
        max_prompt_tokens_per_call=1000,
        num_compactions=1,
    )


def _make_state(**overrides) -> UsageRecorderState:
    """Build a minimal UsageRecorderState. record_usage_event ignores
    ``dal`` (it's the DAL itself being called), so we leave it None.
    Override any field via kwargs."""
    defaults = dict(
        dal=None,
        request_type="user_chat",
        model="m",
        provider="p",
        is_robusta_model=False,
        stats=_stats(),
        iterations=1,
    )
    defaults.update(overrides)
    return UsageRecorderState(**defaults)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────
# record_usage_event
# ──────────────────────────────────────────────────────────────────


class TestRecordUsageEvent:
    def test_no_op_when_dal_disabled(self, mock_dal):
        mock_dal.enabled = False
        mock_dal.record_usage_event(_make_state())
        # No client interaction at all when disabled.
        mock_dal.client.table.assert_not_called()

    def test_inserts_row_with_correct_payload(self, mock_dal):
        mock_dal.record_usage_event(_make_state(
            request_type="user_chat",
            request_source="freeform",
            source_ref="issue-42",
            conversation_id="conv-abc",
            conversation_source="chat_history",
            model="anthropic/claude-sonnet-4-5",
            provider="anthropic",
            is_robusta_model=False,
            iterations=3,
            tool_call_count=5,
            is_streaming=True,
            finish_reason="stop",
            user_id="user-xyz",
            request_id="req-uuid-123",
            meta={"experiment_id": "abc"},
        ))

        # client.table(<table>).insert(<payload>).execute()
        mock_dal.client.table.assert_called_once_with(HOLMES_USAGE_EVENTS_TABLE)
        insert_call = mock_dal.client.table.return_value.insert
        insert_call.assert_called_once()

        payload = insert_call.call_args.args[0]

        # Identity
        assert payload["account_id"] == mock_dal.account_id
        assert payload["cluster_id"] == "test-cluster"
        assert payload["user_id"] == "user-xyz"
        assert payload["conversation_id"] == "conv-abc"
        assert payload["conversation_source"] == "chat_history"
        assert payload["request_id"] == "req-uuid-123"

        # Classification
        assert payload["request_type"] == "user_chat"
        assert payload["request_source"] == "freeform"
        assert payload["source_ref"] == "issue-42"
        assert payload["status"] == "success"   # default on the state
        assert payload["model"] == "anthropic/claude-sonnet-4-5"
        assert payload["provider"] == "anthropic"
        assert payload["is_robusta_model"] is False

        # Stats
        assert payload["prompt_tokens"] == 1000
        assert payload["completion_tokens"] == 234
        assert payload["cached_tokens"] == 50
        assert payload["reasoning_tokens"] == 12
        assert payload["total_tokens"] == 1234
        assert payload["total_cost"] == pytest.approx(0.0123)
        assert payload["num_compactions"] == 1
        assert payload["iterations"] == 3
        assert payload["max_prompt_tokens_per_call"] == 1000
        assert payload["max_completion_tokens_per_call"] == 234

        # Outcome
        assert payload["tool_call_count"] == 5
        # duration_ms is computed from state.t_start at write time, so we
        # don't pin a value — just assert the type contract.
        assert isinstance(payload["duration_ms"], int)
        assert payload["duration_ms"] >= 0
        assert payload["is_streaming"] is True
        assert payload["finish_reason"] == "stop"
        assert payload["meta"] == {"experiment_id": "abc"}

    def test_falls_back_to_dal_cluster_when_cluster_id_not_supplied(self, mock_dal):
        mock_dal.record_usage_event(_make_state())   # cluster_id left None
        payload = mock_dal.client.table.return_value.insert.call_args.args[0]
        assert payload["cluster_id"] == "test-cluster"

    def test_explicit_cluster_id_overrides_dal_default(self, mock_dal):
        mock_dal.record_usage_event(_make_state(cluster_id="other-cluster"))
        payload = mock_dal.client.table.return_value.insert.call_args.args[0]
        assert payload["cluster_id"] == "other-cluster"

    def test_meta_defaults_to_empty_dict_when_none(self, mock_dal):
        # The state defaults meta to {}, so the column should be {} too.
        mock_dal.record_usage_event(_make_state())
        payload = mock_dal.client.table.return_value.insert.call_args.args[0]
        assert payload["meta"] == {}

    def test_swallows_supabase_errors(self, mock_dal):
        # Supabase client raises — record_usage_event must not bubble up.
        mock_dal.client.table.return_value.insert.return_value.execute.side_effect = (
            RuntimeError("supabase down")
        )
        # Should not raise.
        mock_dal.record_usage_event(_make_state())

    def test_handles_stats_with_none_cached_tokens(self, mock_dal):
        # Some providers don't report cached_tokens — should land as NULL.
        stats = RequestStats(
            total_cost=0.001,
            total_tokens=100,
            prompt_tokens=80,
            completion_tokens=20,
            cached_tokens=None,
            reasoning_tokens=0,
        )
        mock_dal.record_usage_event(_make_state(stats=stats))
        payload = mock_dal.client.table.return_value.insert.call_args.args[0]
        assert payload["cached_tokens"] is None

    def test_handles_state_with_no_stats(self, mock_dal):
        # state.stats is None when the request never reached a terminal
        # event with cost data (aborted / pre-LLM error). The DAL must
        # still write a row with zero/NULL token columns.
        mock_dal.record_usage_event(_make_state(stats=None, status="aborted"))
        payload = mock_dal.client.table.return_value.insert.call_args.args[0]
        assert payload["status"] == "aborted"
        assert payload["prompt_tokens"] == 0
        assert payload["completion_tokens"] == 0
        assert payload["total_tokens"] == 0
        assert payload["total_cost"] == 0.0
        assert payload["cached_tokens"] is None


# Feedback writes are no longer Holmes' responsibility — the FE calls the
# public.record_feedback() Postgres function directly via supabase.rpc(...).
# That function lives in the migration script and is verified against a real
# Postgres in the integration suite. There is no Holmes-side code path to
# unit-test here.
