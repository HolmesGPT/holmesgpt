"""E2E tests for the claim RPCs' concurrency + stale-sweep guarantees,
exercised directly against Postgres — no Holmes worker required.

These cover the gaps the worker-driven burst tests cannot:

  * FOR UPDATE SKIP LOCKED — two claimers racing the SAME backlog get
    DISJOINT row sets; no row is ever claimed twice; every claimed row lands
    in 'running'. This is the cross-instance load-balancing guarantee.
  * stale-pending sweep — a pending row older than the claim window is moved
    to 'timeout' (and excluded from the claimable set) the next time any
    claim runs, regardless of which instance triggers it.

Isolation: each test stages rows on a unique synthetic cluster id that no
running worker polls or is broadcast to, and drives the claim RPC itself via
relay-authenticated clients (is_relay() satisfies the Conversations /
RemoteToolCalls RLS for INSERT/UPDATE/SELECT). A live worker for CLUSTER_NAME
therefore never touches these rows, so the suite can run with a worker up.

Requires STORE_* (relay creds) + ROBUSTA_UI_TOKEN/CLUSTER_NAME.

Cleanup note: RLS lets nobody DELETE RemoteToolCalls, and only support/owner
DELETE Conversations, so relay can't remove these rows. They're staged on a
unique per-run synthetic cluster (invisible to real cluster views) and are
swept to terminal states by the claim itself — the same accepted leak pattern
the RemoteToolCalls burst test already relies on (retention cron reclaims them).
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from tests.core.conversations_worker.integration import SupabaseFixture

pytestmark = [pytest.mark.conversation_worker, pytest.mark.integration]


def _iso_cluster(prefix: str) -> str:
    """A synthetic cluster id unique per run — no worker watches it."""
    return f"claimtest-{prefix}-{uuid.uuid4().hex[:12]}"


# ---- Conversations helpers (relay client; is_relay() satisfies RLS) ----


def _insert_pending_conversations(
    rc, account_id: str, cluster_id: str, count: int, updated_at: Optional[str] = None
) -> List[str]:
    rows: List[Dict[str, Any]] = []
    for _ in range(count):
        row = {
            "account_id": account_id,
            "cluster_id": cluster_id,
            "origin": "chat",
            "status": "pending",
            "request_sequence": 1,
            "metadata": {},
        }
        if updated_at is not None:
            # No BEFORE-INSERT trigger on Conversations, so a backdated
            # updated_at sticks (the updated_at trigger is BEFORE UPDATE only).
            row["updated_at"] = updated_at
            row["created_at"] = updated_at
        rows.append(row)
    res = rc.table("Conversations").insert(rows).execute()
    return [r["conversation_id"] for r in (res.data or [])]


def _claim_conversations(rc, account_id, cluster_id, assignee, limit) -> List[dict]:
    return (
        rc.rpc(
            "claim_n_pending_conversations",
            {
                "_account_id": account_id,
                "_cluster_id": cluster_id,
                "_assignee": assignee,
                "_limit": limit,
            },
        ).execute().data
        or []
    )


def _conversation_statuses(rc, ids: List[str]) -> Dict[str, str]:
    rows = (
        rc.table("Conversations")
        .select("conversation_id,status")
        .in_("conversation_id", ids)
        .execute()
    ).data or []
    return {r["conversation_id"]: r["status"] for r in rows}


# ---- RemoteToolCalls helpers (relay client) ----


def _insert_pending_tool_calls(
    rc, account_id, cluster_id, count, updated_at: Optional[str] = None, name="probe"
) -> List[str]:
    rows: List[Dict[str, Any]] = []
    for i in range(count):
        row = {
            "account_id": account_id,
            "source_cluster": cluster_id,
            "target_cluster": cluster_id,
            "status": "pending",
            "tool_request": {
                "tool_name": f"{name}_{i}",
                "tool_params": {},
                "tool_call_id": str(uuid.uuid4()),
                "max_token_count": 1000,
            },
            "metadata": {},
        }
        if updated_at is not None:
            row["updated_at"] = updated_at
            row["created_at"] = updated_at
        rows.append(row)
    res = rc.table("RemoteToolCalls").insert(rows).execute()
    return [r["id"] for r in (res.data or [])]


def _claim_tool_calls(rc, account_id, cluster_id, assignee, limit) -> List[dict]:
    return (
        rc.rpc(
            "claim_n_pending_tool_calls",
            {
                "_account_id": account_id,
                "_cluster_id": cluster_id,
                "_assignee": assignee,
                "_limit": limit,
            },
        ).execute().data
        or []
    )


def _tool_call_statuses(rc, ids: List[str]) -> Dict[str, str]:
    rows = (
        rc.table("RemoteToolCalls")
        .select("id,status")
        .in_("id", ids)
        .execute()
    ).data or []
    return {r["id"]: r["status"] for r in rows}


# ===========================================================================
# SKIP LOCKED: concurrent claimers get disjoint sets
# ===========================================================================


class TestConcurrentClaimingDisjoint:

    _N = 12  # backlog size; both claimers try to grab everything

    def test_concurrent_conversation_claims_are_disjoint(
        self, supabase_fx: SupabaseFixture
    ):
        """Two relay clients claim the SAME conversation backlog concurrently,
        each asking for all N rows. FOR UPDATE SKIP LOCKED must partition the
        rows: no conversation claimed by both, none claimed twice, and every
        claimed row ends 'running'."""
        rc = supabase_fx._relay()
        cluster = _iso_cluster("conv-disjoint")
        ids = _insert_pending_conversations(
            rc, supabase_fx.account_id, cluster, self._N
        )
        assert len(ids) == self._N

        rc_a = supabase_fx.new_relay_client()
        rc_b = supabase_fx.new_relay_client()
        # Each claimer asks for the full backlog, so without SKIP LOCKED they
        # would overlap. With it, they partition the rows.
        with ThreadPoolExecutor(max_workers=2) as ex:
            fa = ex.submit(
                _claim_conversations,
                rc_a, supabase_fx.account_id, cluster, "claimer-a", self._N,
            )
            fb = ex.submit(
                _claim_conversations,
                rc_b, supabase_fx.account_id, cluster, "claimer-b", self._N,
            )
            claimed_a = [r["conversation_id"] for r in fa.result()]
            claimed_b = [r["conversation_id"] for r in fb.result()]

        set_a, set_b = set(claimed_a), set(claimed_b)
        # No duplicates within either claimer's own result set.
        assert len(claimed_a) == len(set_a), f"claimer-a returned dupes: {claimed_a}"
        assert len(claimed_b) == len(set_b), f"claimer-b returned dupes: {claimed_b}"
        # Disjoint: the load-bearing SKIP LOCKED assertion.
        overlap = set_a & set_b
        assert not overlap, f"same conversation claimed by both claimers: {overlap}"
        # Between them they took the whole backlog, all now 'running'.
        assert set_a | set_b == set(ids)
        statuses = _conversation_statuses(rc, ids)
        not_running = {c: s for c, s in statuses.items() if s != "running"}
        assert not not_running, f"claimed rows not 'running': {not_running}"

    def test_concurrent_tool_call_claims_are_disjoint(
        self, supabase_fx: SupabaseFixture
    ):
        """Same SKIP LOCKED guarantee for RemoteToolCalls."""
        rc = supabase_fx._relay()
        cluster = _iso_cluster("tc-disjoint")
        ids = _insert_pending_tool_calls(
            rc, supabase_fx.account_id, cluster, self._N
        )
        supabase_fx._created_tool_calls.extend(ids)
        assert len(ids) == self._N

        rc_a = supabase_fx.new_relay_client()
        rc_b = supabase_fx.new_relay_client()
        with ThreadPoolExecutor(max_workers=2) as ex:
            fa = ex.submit(
                _claim_tool_calls,
                rc_a, supabase_fx.account_id, cluster, "claimer-a", self._N,
            )
            fb = ex.submit(
                _claim_tool_calls,
                rc_b, supabase_fx.account_id, cluster, "claimer-b", self._N,
            )
            claimed_a = [r["id"] for r in fa.result()]
            claimed_b = [r["id"] for r in fb.result()]

        set_a, set_b = set(claimed_a), set(claimed_b)
        assert len(claimed_a) == len(set_a), f"claimer-a returned dupes: {claimed_a}"
        assert len(claimed_b) == len(set_b), f"claimer-b returned dupes: {claimed_b}"
        overlap = set_a & set_b
        assert not overlap, f"same tool call claimed by both claimers: {overlap}"
        assert set_a | set_b == set(ids)
        statuses = _tool_call_statuses(rc, ids)
        not_running = {t: s for t, s in statuses.items() if s != "running"}
        assert not not_running, f"claimed rows not 'running': {not_running}"


# ===========================================================================
# Stale-pending sweep: old pending rows are timed out, not claimed
# ===========================================================================


class TestStalePendingSweep:

    def test_stale_pending_conversation_swept_to_timeout(
        self, supabase_fx: SupabaseFixture
    ):
        """A pending conversation older than the 1-hour claim window must be
        swept to 'timeout' (not claimed) on the next claim, while a fresh
        pending row in the same call is claimed to 'running'."""
        rc = supabase_fx._relay()
        cluster = _iso_cluster("conv-sweep")
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        stale_ids = _insert_pending_conversations(
            rc, supabase_fx.account_id, cluster, 1, updated_at=stale_ts
        )
        fresh_ids = _insert_pending_conversations(
            rc, supabase_fx.account_id, cluster, 2
        )
        stale_id = stale_ids[0]

        # Confirm the backdate stuck (no INSERT trigger should reset it); if a
        # future trigger ever does, skip rather than silently pass.
        got = (
            rc.table("Conversations")
            .select("updated_at")
            .eq("conversation_id", stale_id)
            .single()
            .execute()
        ).data
        age = datetime.now(timezone.utc) - datetime.fromisoformat(got["updated_at"])
        if age < timedelta(hours=1):
            pytest.skip(
                f"backdated updated_at did not stick (age={age}); cannot stage "
                "a stale row in this environment"
            )

        claimed = _claim_conversations(
            rc, supabase_fx.account_id, cluster, "sweeper", 10
        )
        claimed_ids = {r["conversation_id"] for r in claimed}

        assert stale_id not in claimed_ids, "stale pending row must not be claimed"
        assert set(fresh_ids) <= claimed_ids, "fresh pending rows must be claimed"

        statuses = _conversation_statuses(rc, [stale_id, *fresh_ids])
        assert statuses[stale_id] == "timeout", (
            f"stale pending conversation should be swept to 'timeout', "
            f"got {statuses[stale_id]}"
        )
        for fid in fresh_ids:
            assert statuses[fid] == "running", (
                f"fresh conversation {fid} should be 'running', got {statuses[fid]}"
            )

    def test_stale_pending_tool_call_swept_to_timeout(
        self, supabase_fx: SupabaseFixture
    ):
        """A pending tool call older than the 5-minute claim window must be
        swept to 'timeout' (not claimed), while a fresh one is claimed."""
        rc = supabase_fx._relay()
        cluster = _iso_cluster("tc-sweep")
        stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

        stale_ids = _insert_pending_tool_calls(
            rc, supabase_fx.account_id, cluster, 1, updated_at=stale_ts, name="stale"
        )
        fresh_ids = _insert_pending_tool_calls(
            rc, supabase_fx.account_id, cluster, 2, name="fresh"
        )
        supabase_fx._created_tool_calls.extend(stale_ids + fresh_ids)
        stale_id = stale_ids[0]

        got = (
            rc.table("RemoteToolCalls")
            .select("updated_at")
            .eq("id", stale_id)
            .single()
            .execute()
        ).data
        age = datetime.now(timezone.utc) - datetime.fromisoformat(got["updated_at"])
        if age < timedelta(minutes=5):
            pytest.skip(
                f"backdated updated_at did not stick (age={age}); cannot stage "
                "a stale row in this environment"
            )

        claimed = _claim_tool_calls(rc, supabase_fx.account_id, cluster, "sweeper", 10)
        claimed_ids = {r["id"] for r in claimed}

        assert stale_id not in claimed_ids, "stale pending tool call must not be claimed"
        assert set(fresh_ids) <= claimed_ids, "fresh pending tool calls must be claimed"

        statuses = _tool_call_statuses(rc, [stale_id, *fresh_ids])
        assert statuses[stale_id] == "timeout", (
            f"stale pending tool call should be swept to 'timeout', "
            f"got {statuses[stale_id]}"
        )
        for fid in fresh_ids:
            assert statuses[fid] == "running", (
                f"fresh tool call {fid} should be 'running', got {statuses[fid]}"
            )
