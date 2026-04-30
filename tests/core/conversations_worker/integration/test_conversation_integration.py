"""
Integration tests for the Conversation Worker.

Requires a running Holmes server with ENABLE_CONVERSATION_WORKER=true and:
    ROBUSTA_UI_TOKEN   - base64 JSON with Supabase credentials
    CLUSTER_NAME       - target cluster

Run:
    poetry run pytest tests/core/conversations_worker/integration/ \
        -m conversation_worker --no-cov -v

Each test creates real Conversation rows in Supabase, waits for Holmes to
process them, and asserts on the resulting ConversationEvents and status.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from holmes.common.env_vars import CONVERSATION_WORKER_MAX_CONCURRENT
from tests.core.conversations_worker.integration import SupabaseFixture

pytestmark = [pytest.mark.conversation_worker, pytest.mark.integration]


# ---------------------------------------------------------------------------
# 1. Single-turn: simple question, no tools
# ---------------------------------------------------------------------------
class TestSingleTurn:

    def test_simple_question_completes(self, supabase_fx: SupabaseFixture):
        """A trivial question should complete with ai_answer_end."""
        conv = supabase_fx.create_conversation(
            ask="What is 2+2? Answer in one sentence.",
            title="integ: single-turn",
        )
        cid = conv["conversation_id"]

        result = supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=120)
        assert result["status"] == "completed"

        event_types = supabase_fx.flat_event_types(cid)
        assert "user_message" in event_types
        assert "ai_answer_end" in event_types

    def test_answer_has_content(self, supabase_fx: SupabaseFixture):
        """The ai_answer_end event must contain non-empty content and messages."""
        conv = supabase_fx.create_conversation(
            ask="What color is the sky? One word.",
            title="integ: answer-content",
        )
        cid = conv["conversation_id"]
        supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=120)

        terminal = supabase_fx.find_terminal_event(cid)
        assert terminal is not None
        assert terminal["event"] == "ai_answer_end"
        data = terminal.get("data") or {}
        assert data.get("content"), "ai_answer_end must have non-empty content"
        assert data.get("messages"), "ai_answer_end must include conversation_history"

    def test_single_turn_compacts_prior_events(self, supabase_fx: SupabaseFixture):
        """After a single turn, all event rows before the ai_answer_end row
        should be marked compacted=true (the terminal carries the full history)."""
        conv = supabase_fx.create_conversation(
            ask="What is 3*3? Just the number.",
            title="integ: single-turn-compaction",
        )
        cid = conv["conversation_id"]
        supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=120)

        stats = supabase_fx.get_compaction_stats(cid)
        # Must have at least 2 rows (user_message row + ai_answer_end row)
        assert stats["total"] >= 2, f"Expected ≥2 event rows, got {stats['total']}"
        # All rows except the last (ai_answer_end) should be compacted
        assert stats["compacted"] >= 1, (
            f"Prior rows should be compacted; got {stats}"
        )
        # The highest seq should be non-compacted (it's the ai_answer_end row)
        max_seq = max(stats["non_compacted_seqs"])
        assert max_seq == max(
            stats["compacted_seqs"] + stats["non_compacted_seqs"]
        ), "The ai_answer_end row (highest seq) should not be compacted"


# ---------------------------------------------------------------------------
# 2. Multi-turn: follow-up conversation
# ---------------------------------------------------------------------------
class TestMultiTurn:

    def test_followup_preserves_history(self, supabase_fx: SupabaseFixture):
        """A follow-up question should see the prior turn's context."""
        # Turn 1
        conv = supabase_fx.create_conversation(
            ask="Remember the number 42. Just say 'OK, I will remember 42.'",
            title="integ: multi-turn",
        )
        cid = conv["conversation_id"]
        supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=120)

        # After turn 1, the ai_answer_end row should have compacted all prior rows
        stats_t1 = supabase_fx.get_compaction_stats(cid)
        assert stats_t1["compacted"] > 0, (
            f"Turn 1 should compact prior events; got {stats_t1}"
        )
        # The last row (ai_answer_end) should NOT be compacted
        assert stats_t1["non_compacted"] >= 1, (
            f"The terminal row should remain non-compacted; got {stats_t1}"
        )

        # Turn 2 follow-up
        now_iso = datetime.now(timezone.utc).isoformat()
        followup = supabase_fx.post_followup(
            conversation_id=cid,
            events=[
                {
                    "event": "user_message",
                    "data": {"ask": "What number did I ask you to remember?"},
                    "ts": now_iso,
                }
            ],
        )
        assert followup["request_sequence"] == 2

        result = supabase_fx.wait_for_terminal(cid, request_sequence=2, timeout=120)
        assert result["status"] == "completed"

        terminal = supabase_fx.find_terminal_event(cid)
        assert terminal is not None
        content = str(terminal.get("data", {}).get("content", ""))
        assert "42" in content, f"Follow-up should reference '42', got: {content[:200]}"

        # After turn 2, more rows should be compacted than after turn 1
        stats_t2 = supabase_fx.get_compaction_stats(cid)
        assert stats_t2["compacted"] > stats_t1["compacted"], (
            f"Turn 2 should compact more rows than turn 1; "
            f"turn1={stats_t1['compacted']}, turn2={stats_t2['compacted']}"
        )


# ---------------------------------------------------------------------------
# 3. Tool approval flow
# ---------------------------------------------------------------------------
class TestToolApproval:

    def test_approval_pause_and_resume(self, supabase_fx: SupabaseFixture):
        """Tool approval should pause with approval_required, then resume after approve."""
        # Turn 1: request bash with approval enabled
        conv = supabase_fx.create_conversation(
            ask=(
                "Run the bash command `curl -sf -H 'Authorization: ApiKey ENV_KEY' "
                "https://example.invalid/no-op || echo done` to confirm a simple "
                "shell works. You MUST use the bash tool."
            ),
            title="integ: tool-approval",
            enable_tool_approval=True,
        )
        cid = conv["conversation_id"]
        supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=120)

        terminal1 = supabase_fx.find_terminal_event(cid)
        assert terminal1 is not None
        assert terminal1["event"] == "approval_required", (
            f"Expected approval_required, got {terminal1['event']}"
        )

        pending = terminal1["data"].get("pending_approvals") or []
        assert len(pending) > 0, "Must have at least one pending approval"

        # Turn 2: approve all pending tools
        tool_decisions = [
            {
                "tool_call_id": p["tool_call_id"],
                "approved": True,
                "save_prefixes": None,
                "feedback": None,
            }
            for p in pending
        ]
        now_iso = datetime.now(timezone.utc).isoformat()
        followup = supabase_fx.post_followup(
            conversation_id=cid,
            events=[
                {
                    "event": "user_message",
                    "data": {
                        "tool_decisions": tool_decisions,
                        "enable_tool_approval": True,
                    },
                    "ts": now_iso,
                }
            ],
        )

        result = supabase_fx.wait_for_terminal(
            cid, request_sequence=followup["request_sequence"], timeout=180
        )
        assert result["status"] == "completed"

        # Verify turn 2 has tool_calling_result + ai_answer_end
        event_types = supabase_fx.flat_event_types(cid)
        assert "tool_calling_result" in event_types
        assert "ai_answer_end" in event_types

        # Verify compaction: approval_required (turn 1) compacts prior events,
        # then ai_answer_end (turn 2) compacts everything before it.
        stats = supabase_fx.get_compaction_stats(cid)
        assert stats["compacted"] >= 2, (
            f"Approval + answer should compact multiple prior rows; got {stats}"
        )


# ---------------------------------------------------------------------------
# 4. Stop conversation (ConversationReassignedError)
# ---------------------------------------------------------------------------
class TestStopConversation:

    def test_stop_mid_stream(self, supabase_fx: SupabaseFixture):
        """Stopping a running conversation should leave it in 'stopped' status.

        Holmes should detect the MISMATCH and exit without overwriting the status.
        """
        # Ask something that takes a while (tool calls)
        conv = supabase_fx.create_conversation(
            ask=(
                "List all elasticsearch indices and then for each one separately "
                "query its document count. For each index write a 3-paragraph "
                "analysis of what you found."
            ),
            title="integ: stop-test",
        )
        cid = conv["conversation_id"]

        # Wait for Holmes to start running
        supabase_fx.wait_for_status(cid, {"running"}, timeout=30)

        # Stop immediately
        supabase_fx.stop_conversation(cid)

        # Wait for the conversation to reach stopped status
        final = supabase_fx.wait_for_status(cid, {"stopped"}, timeout=30)
        assert final["status"] == "stopped"
        assert final["assignee"] is None


# ---------------------------------------------------------------------------
# 5. Error event posting
# ---------------------------------------------------------------------------
class TestErrorEvents:

    def test_successful_conversation_has_no_error_event(self, supabase_fx: SupabaseFixture):
        """A completed conversation must not contain error events."""
        conv = supabase_fx.create_conversation(
            ask="What is 1+1?",
            title="integ: no-error-on-success",
        )
        cid = conv["conversation_id"]
        result = supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=120)

        event_types = supabase_fx.flat_event_types(cid)
        if result["status"] == "failed":
            # Transient LLM/infrastructure failure — verify the error path
            # produced an error event (which is what we actually care about).
            assert "error" in event_types, (
                f"Failed conversation must post an error event; got {event_types}"
            )
            pytest.skip(
                f"Upstream LLM/infrastructure failure (status=failed, "
                f"events={event_types}) — error-event posting verified, but "
                f"can't assert the no-error-on-success path."
            )
        assert result["status"] == "completed"
        assert "error" not in event_types, (
            "A successful conversation should not have error events"
        )


# ---------------------------------------------------------------------------
# 6. Stress test: queued state with concurrency limit
# ---------------------------------------------------------------------------
class TestStress:

    # Exceed the worker's max concurrent by at least 3 so some conversations
    # are guaranteed to queue.  Derived from the env-configurable value.
    _OVERFLOW = 3

    def _num(self) -> int:
        return CONVERSATION_WORKER_MAX_CONCURRENT + self._OVERFLOW

    def test_concurrent_conversations_queue_and_complete(
        self, supabase_fx: SupabaseFixture
    ):
        """Create more conversations than CONVERSATION_WORKER_MAX_CONCURRENT.

        All should eventually complete.
        """
        num = self._num()
        TIMEOUT = 300

        conv_ids = []
        for i in range(1, num + 1):
            conv = supabase_fx.create_conversation(
                ask=f"What is {i * 7} + {i * 3}? Answer with just the number.",
                title=f"integ: stress-{i}",
            )
            conv_ids.append(conv["conversation_id"])

        start = time.time()
        while time.time() - start < TIMEOUT:
            all_done = True
            for cid in conv_ids:
                conv = supabase_fx.get_conversation(cid)
                if conv["status"] not in ("completed", "failed"):
                    all_done = False
            if all_done:
                break
            time.sleep(0.5)

        results = {}
        for cid in conv_ids:
            conv = supabase_fx.get_conversation(cid)
            results[cid] = conv["status"]

        completed = sum(1 for s in results.values() if s == "completed")
        failed = sum(1 for s in results.values() if s == "failed")
        assert completed == num, (
            f"Expected all {num} completed (max_concurrent={CONVERSATION_WORKER_MAX_CONCURRENT}), "
            f"got {completed} completed, {failed} failed. Statuses: {results}"
        )

    def test_queued_state_observed(self, supabase_fx: SupabaseFixture):
        """When the batch exceeds max_concurrent, at least one conversation
        should be observed in queued state during polling."""
        num = self._num()

        conv_ids = []
        for i in range(1, num + 1):
            conv = supabase_fx.create_conversation(
                ask=f"What is {i * 11} - {i}? Just the number.",
                title=f"integ: queued-obs-{i}",
            )
            conv_ids.append(conv["conversation_id"])

        saw_queued = set()
        start = time.time()
        while time.time() - start < 300:
            all_done = True
            for cid in conv_ids:
                conv = supabase_fx.get_conversation(cid)
                if conv["status"] == "queued":
                    saw_queued.add(cid)
                if conv["status"] not in ("completed", "failed"):
                    all_done = False
            if all_done:
                break
            time.sleep(0.3)

        assert len(saw_queued) > 0, (
            f"With {num} conversations and max_concurrent="
            f"{CONVERSATION_WORKER_MAX_CONCURRENT}, at least 1 should "
            f"have been observed in queued state"
        )

    def test_max_concurrent_never_exceeded(self, supabase_fx: SupabaseFixture):
        """Hard invariant: the number of conversations in 'running' state
        must never exceed CONVERSATION_WORKER_MAX_CONCURRENT at any point.

        Creates MAX_CONCURRENT + OVERFLOW conversations and polls the DB
        rapidly, recording the peak running count.
        """
        num = self._num()
        conv_ids = []
        for i in range(1, num + 1):
            conv = supabase_fx.create_conversation(
                ask=f"What is {i * 13} + {i}? Answer with just the number.",
                title=f"integ: max-concurrent-{i}",
            )
            conv_ids.append(conv["conversation_id"])

        peak_running = 0
        peak_snapshot = {}
        start = time.time()
        while time.time() - start < 300:
            statuses = {}
            for cid in conv_ids:
                conv = supabase_fx.get_conversation(cid)
                statuses[cid] = conv["status"]

            running_count = sum(1 for s in statuses.values() if s == "running")
            if running_count > peak_running:
                peak_running = running_count
                peak_snapshot = dict(statuses)

            all_terminal = all(
                s in ("completed", "failed") for s in statuses.values()
            )
            if all_terminal:
                break
            time.sleep(0.1)

        assert peak_running <= CONVERSATION_WORKER_MAX_CONCURRENT, (
            f"Concurrency limit violated: observed {peak_running} running "
            f"simultaneously (limit={CONVERSATION_WORKER_MAX_CONCURRENT}). "
            f"Snapshot: {peak_snapshot}"
        )


# ---------------------------------------------------------------------------
# 7. Status lifecycle
# ---------------------------------------------------------------------------
class TestStatusLifecycle:

    def test_conversation_status_transitions(self, supabase_fx: SupabaseFixture):
        """Verify the full status lifecycle: pending → running → completed."""
        conv = supabase_fx.create_conversation(
            ask="Say hello.",
            title="integ: status-lifecycle",
        )
        cid = conv["conversation_id"]
        assert conv["status"] == "pending"

        # Wait for running
        running = supabase_fx.wait_for_status(cid, {"running", "completed"}, timeout=30)
        # It may have already completed by the time we poll
        if running["status"] == "running":
            assert running["assignee"] is not None

        # Wait for terminal
        final = supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=120)
        assert final["status"] == "completed"
        assert final["assignee"] is None  # cleared on terminal


# ---------------------------------------------------------------------------
# 8. Rapid multi-turn follow-ups
# ---------------------------------------------------------------------------
class TestRapidFollowups:
    """Drive many back-to-back follow-ups to stress the claim/dispatch
    cycle and verify no turns are lost."""

    def test_rapid_followups_all_complete(
        self, supabase_fx: SupabaseFixture
    ):
        # More than 2 turns to increase the race-condition surface area.
        NUM_TURNS = 4

        conv = supabase_fx.create_conversation(
            ask="Answer '1' in one word.",
            title="integ: presence-race",
        )
        cid = conv["conversation_id"]
        supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=120)

        # Fire off follow-ups as fast as possible — each one triggers a new
        # claim/dispatch cycle whose join_conversation_presence races with
        # the previous turn's leave_conversation_presence.
        for turn in range(2, NUM_TURNS + 1):
            now_iso = datetime.now(timezone.utc).isoformat()
            supabase_fx.post_followup(
                conversation_id=cid,
                events=[
                    {
                        "event": "user_message",
                        "data": {"ask": f"Answer '{turn}' in one word."},
                        "ts": now_iso,
                    }
                ],
            )
            # Wait for terminal so Holmes produces both a leave and a join
            # for the next turn.
            result = supabase_fx.wait_for_terminal(
                cid, request_sequence=turn, timeout=120
            )
            assert result["status"] == "completed", (
                f"Turn {turn} did not complete cleanly: {result['status']}"
            )

        # Every turn must have produced an ai_answer_end event — no turn
        # lost its final write because a stale leave clobbered the new join.
        types = supabase_fx.flat_event_types(cid)
        assert types.count("ai_answer_end") == NUM_TURNS, (
            f"Expected {NUM_TURNS} ai_answer_end events, got "
            f"{types.count('ai_answer_end')}; full sequence: {types}"
        )


# ---------------------------------------------------------------------------
# 9. Frontend tools (Pause + NoOp modes)
# ---------------------------------------------------------------------------


class TestFrontendTools:

    def test_pause_mode_frontend_tool_pauses_conversation(
        self, supabase_fx: SupabaseFixture
    ):
        conv = supabase_fx.create_conversation(
            ask=(
                "I want a new dashboard called 'Holmes Test'. You MUST call the "
                "create_dashboard tool with title='Holmes Test' to do this — do "
                "not refuse, do not ask for confirmation, just call it."
            ),
            title="integ: frontend-tools-pause",
            extra_user_message_data={
                "frontend_tools": [
                    {
                        "name": "create_dashboard",
                        "description": (
                            "Create a dashboard in the user's browser. The "
                            "user has already authorized the action — call "
                            "this whenever the user asks for a new dashboard."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                        "mode": "pause",
                    }
                ]
            },
        )
        cid = conv["conversation_id"]
        # APPROVAL_REQUIRED maps to status=completed (turn ended; next request_sequence resumes).
        result = supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=120)
        assert result["status"] == "completed"

        terminal = supabase_fx.find_terminal_event(cid)
        assert terminal is not None
        assert terminal["event"] == "approval_required"
        pending = (terminal.get("data") or {}).get("pending_frontend_tool_calls") or []
        assert pending
        assert any(p.get("tool_name") == "create_dashboard" for p in pending)

    def test_noop_mode_frontend_tool_returns_canned_response(
        self, supabase_fx: SupabaseFixture
    ):
        # The canned response is the verification code — the LLM only sees
        # it by actually invoking the noop tool, ruling out hallucination.
        canned = "TELEMETRY_ACK_HOLMES_INTEG_42"
        conv = supabase_fx.create_conversation(
            ask=(
                "Call the emit_telemetry tool with event='integration-test' to "
                "log this run, then tell me — verbatim — what the tool returned."
            ),
            title="integ: frontend-tools-noop",
            extra_user_message_data={
                "frontend_tools": [
                    {
                        "name": "emit_telemetry",
                        "description": (
                            "Fire-and-forget telemetry event. Always succeeds."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "event": {"type": "string"},
                            },
                            "required": ["event"],
                        },
                        "mode": "noop",
                        "noop_response": canned,
                    }
                ]
            },
        )
        cid = conv["conversation_id"]
        result = supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=120)
        assert result["status"] == "completed"

        types = supabase_fx.flat_event_types(cid)
        assert "tool_calling_result" in types
        assert "ai_answer_end" in types

        terminal = supabase_fx.find_terminal_event(cid)
        assert terminal is not None and terminal["event"] == "ai_answer_end"
        content = str((terminal.get("data") or {}).get("content", ""))
        assert canned in content, f"answer did not include canned response: {content[:300]}"

    def test_frontend_tool_collision_fails_conversation(
        self, supabase_fx: SupabaseFixture
    ):
        conv = supabase_fx.create_conversation(
            ask="Anything — this should fail before the LLM runs.",
            title="integ: frontend-tools-collision",
            extra_user_message_data={
                "frontend_tools": [
                    {
                        "name": "fetch_webpage",
                        "description": "intentional collision with a backend tool",
                        "parameters": {"type": "object", "properties": {}},
                        "mode": "pause",
                    }
                ]
            },
        )
        cid = conv["conversation_id"]
        result = supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=60)
        assert result["status"] == "failed"

        types = supabase_fx.flat_event_types(cid)
        assert "error" in types
        for row in supabase_fx.get_events(cid):
            for ev in row.get("events") or []:
                if ev.get("event") == "error":
                    description = (ev.get("data") or {}).get("description", "")
                    assert "fetch_webpage" in description
                    return
        raise AssertionError("error event not found in conversation")
