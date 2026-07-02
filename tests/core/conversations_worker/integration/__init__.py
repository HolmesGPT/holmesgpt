"""Shared fixtures for conversation worker integration tests.

These tests require a running Holmes server with ENABLE_CONVERSATION_WORKER=true
and the following environment variables:

    ROBUSTA_UI_TOKEN     – base64-encoded JSON with store_url, api_key, email,
                           password, account_id
    CLUSTER_NAME         – cluster to target (must match Holmes's config)

Run with:
    poetry run pytest tests/core/conversations_worker/integration/ -m conversation_worker --no-cov -v
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from realtime._async.client import AsyncRealtimeClient
from supabase import create_client, Client
from supabase.lib.client_options import SyncClientOptions as ClientOptions

from holmes.core.conversations_worker.realtime_manager import (
    broadcast_submit_topic,
)


def _decode_token() -> dict:
    raw = os.environ.get("ROBUSTA_UI_TOKEN")
    if not raw:
        pytest.skip("ROBUSTA_UI_TOKEN not set")
    return json.loads(base64.b64decode(raw))


@dataclass
class SupabaseFixture:
    """Thin wrapper around a logged-in Supabase client with helper methods."""

    client: Client
    account_id: str
    cluster_id: str
    user_id: str
    _store_url: str = ""
    _api_key: str = ""
    use_pgchanges: bool = True

    # Track conversation IDs for cleanup
    _created_conversations: list = field(default_factory=list)
    # Track RemoteToolCalls IDs created during the test (best-effort only — the
    # table has no DELETE RLS policy, so rows are reclaimed by the retention job).
    _created_tool_calls: list = field(default_factory=list)
    # Lazily-built Supabase client authenticated as relay (STORE_* creds).
    # RemoteToolCalls INSERT/SELECT are gated on is_relay()/API-role, which the
    # ordinary UI test user does not satisfy.
    _relay_client: Any = field(default=None, repr=False)

    # Persistent Realtime connection for broadcast mode (lazy-initialized).
    _broadcast_loop: Any = field(default=None, repr=False)
    _broadcast_thread: Any = field(default=None, repr=False)
    _broadcast_ch: Any = field(default=None, repr=False)
    _broadcast_setup_error: Optional[BaseException] = field(default=None, repr=False)

    # ---- conversation helpers ----

    def create_conversation(
        self,
        ask: str,
        title: str = "integration test",
        enable_tool_approval: bool = False,
        extra_user_message_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        user_msg_data: Dict[str, Any] = {"ask": ask}
        if enable_tool_approval:
            user_msg_data["enable_tool_approval"] = True
        if extra_user_message_data:
            user_msg_data.update(extra_user_message_data)
        conv = self.client.rpc(
            "post_new_conversation",
            {
                "_account_id": self.account_id,
                "_cluster_id": self.cluster_id,
                "_origin": "chat",
                "_user_id": self.user_id,
                "_title": title,
                "_initial_events": [
                    {"event": "user_message", "data": user_msg_data, "ts": now_iso}
                ],
            },
        ).execute().data
        self._created_conversations.append(conv["conversation_id"])
        # In broadcast mode, the initiator must notify Holmes explicitly.
        if not self.use_pgchanges:
            self.broadcast_submit(conv["conversation_id"])
        return conv

    def post_followup(
        self,
        conversation_id: str,
        events: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        result = self.client.rpc(
            "post_conversation_followup",
            {
                "_account_id": self.account_id,
                "_conversation_id": conversation_id,
                "_events": events,
                "_metadata": metadata or {},
            },
        ).execute().data
        # In broadcast mode, notify Holmes after the follow-up too —
        # followups re-pend the conversation just like initial creation.
        if not self.use_pgchanges:
            self.broadcast_submit(conversation_id)
        return result

    def _ensure_broadcast_channel(self) -> None:
        """Lazy-initialize a persistent Realtime connection + channel for
        broadcast mode.  Runs an asyncio event loop in a daemon thread so
        the sync test code can schedule coroutines on it."""
        if self._broadcast_ch is not None:
            return

        ready = threading.Event()

        def _run_loop() -> None:
            loop = asyncio.new_event_loop()
            self._broadcast_loop = loop

            async def _setup() -> None:
                store_url = self._store_url.rstrip("/")
                if store_url.startswith("https://"):
                    ws_url = "wss://" + store_url[len("https://"):]
                elif store_url.startswith("http://"):
                    ws_url = "ws://" + store_url[len("http://"):]
                else:
                    ws_url = store_url
                # AsyncRealtimeClient appends "/websocket" itself.
                ws_url = f"{ws_url}/realtime/v1"

                topic = broadcast_submit_topic(self.account_id, self.cluster_id)
                rt = AsyncRealtimeClient(
                    url=ws_url, token=self._api_key, auto_reconnect=True
                )
                await rt.connect()
                # Authenticate as the logged-in test user so the realtime.messages
                # RLS policies (which gate on is_account_user_role / cluster perms)
                # can resolve. Without this, the channel runs as anon and the join
                # is rejected — Supabase drops the WS with code 1006.
                session = self.client.auth.get_session()
                if session and session.access_token:
                    await rt.set_auth(session.access_token)
                ch = rt.channel(
                    topic,
                    {"config": {"private": True, "presence": {"enabled": False}}},
                )
                subscribed = asyncio.Event()

                def _on_sub(status: Any, err: Any = None) -> None:
                    if "SUBSCRIBED" in str(status).upper():
                        subscribed.set()

                await ch.subscribe(_on_sub)
                try:
                    await asyncio.wait_for(subscribed.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                self._broadcast_ch = ch
                ready.set()
                # Keep the loop alive so the WS stays open
                while True:
                    await asyncio.sleep(1)

            try:
                loop.run_until_complete(_setup())
            except BaseException as e:
                # Capture so the main thread can re-raise; setting ready
                # unblocks the caller immediately instead of timing out.
                self._broadcast_setup_error = e
                ready.set()

        t = threading.Thread(target=_run_loop, daemon=True)
        t.start()
        self._broadcast_thread = t
        ready.wait(timeout=10)
        if not ready.is_set() or self._broadcast_ch is None:
            raise RuntimeError(
                f"broadcast channel setup failed: {self._broadcast_setup_error!r}"
            )

    def broadcast_submit(self, conversation_id: str) -> None:
        """Send a Broadcast message on the Holmes submit channel.

        Uses a persistent Realtime connection (one WS for all broadcasts).
        """
        self._ensure_broadcast_channel()
        future = asyncio.run_coroutine_threadsafe(
            self._broadcast_ch.send_broadcast(
                "pending_conversations",
                {"conversation_id": conversation_id},
            ),
            self._broadcast_loop,
        )
        future.result(timeout=5)

    # ---- remote tool call helpers ----

    def new_relay_client(self) -> Client:
        """A FRESH (uncached) relay-authenticated client. Each call opens its
        own connection so concurrent claim RPCs can be driven from separate
        threads (one connection per thread). Skips if relay creds aren't set."""
        url = os.environ.get("STORE_URL")
        key = os.environ.get("STORE_API_KEY")
        user = os.environ.get("STORE_USER")
        password = os.environ.get("STORE_PASSWORD")
        if not all([url, key, user, password]):
            pytest.skip(
                "STORE_URL/STORE_API_KEY/STORE_USER/STORE_PASSWORD required for "
                "relay-authenticated claim tests"
            )
        options = ClientOptions(postgrest_client_timeout=60)
        rc = create_client(url, key, options)
        res = rc.auth.sign_in_with_password({"email": user, "password": password})
        rc.auth.set_session(res.session.access_token, res.session.refresh_token)
        rc.postgrest.auth(res.session.access_token)
        return rc

    def _relay(self) -> Client:
        """Cached relay-authenticated client. RemoteToolCalls INSERT/SELECT are
        gated on is_relay()/API-role, which the UI test user lacks."""
        if self._relay_client is None:
            self._relay_client = self.new_relay_client()
        return self._relay_client

    def create_remote_tool_call(
        self,
        tool_name: str = "noop_probe_tool",
        tool_params: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Insert a pending RemoteToolCalls row and broadcast 'pending_tool_calls'
        to wake the worker. The default tool is unknown, so the worker resolves
        it to a terminal result fast — the row still goes pending -> running ->
        completed. Returns the new row id."""
        tool_request: Dict[str, Any] = {
            "tool_name": tool_name,
            "tool_params": tool_params or {},
            "tool_call_id": str(uuid.uuid4()),
            "max_token_count": 1000,
        }
        new_id = self._relay().rpc(
            "post_remote_tool_call_request_and_broadcast",
            {
                "_account_id": self.account_id,
                "_source_cluster": self.cluster_id,
                "_target_cluster": self.cluster_id,
                "_tool_request": tool_request,
                "_user_id": self.user_id,
                "_metadata": metadata or {},
            },
        ).execute().data
        self._created_tool_calls.append(new_id)
        return new_id

    def insert_pending_remote_tool_calls(
        self, count: int, tool_name_prefix: str = "noop_probe_tool"
    ) -> List[str]:
        """Bulk-insert ``count`` pending RemoteToolCalls in ONE request, WITHOUT
        broadcasting — stages a full backlog the worker won't claim until
        broadcast_pending_tool_calls() wakes it. Returns the new row ids."""
        rc = self._relay()
        rows = [
            {
                "account_id": self.account_id,
                "source_cluster": self.cluster_id,
                "target_cluster": self.cluster_id,
                "user_id": self.user_id,
                "status": "pending",
                "tool_request": {
                    "tool_name": f"{tool_name_prefix}_{i}",
                    "tool_params": {},
                    "tool_call_id": str(uuid.uuid4()),
                    "max_token_count": 1000,
                },
                "metadata": {},
            }
            for i in range(count)
        ]
        res = rc.table("RemoteToolCalls").insert(rows).execute()
        ids = [r["id"] for r in (res.data or [])]
        self._created_tool_calls.extend(ids)
        return ids

    def broadcast_pending_tool_calls(self) -> None:
        """Send a 'pending_tool_calls' Broadcast on the Holmes submit channel to
        wake the ToolCallWorker (mirrors what relay's platform-mcp does)."""
        self._ensure_broadcast_channel()
        future = asyncio.run_coroutine_threadsafe(
            self._broadcast_ch.send_broadcast("pending_tool_calls", {}),
            self._broadcast_loop,
        )
        future.result(timeout=5)

    def get_remote_tool_call(self, tool_call_id: str) -> Dict[str, Any]:
        return (
            self._relay()
            .table("RemoteToolCalls")
            .select("*")
            .eq("id", tool_call_id)
            .single()
            .execute()
        ).data

    def get_remote_tool_call_statuses(self, ids: List[str]) -> Dict[str, str]:
        """Atomic single-query snapshot of many tool calls' statuses."""
        rows = (
            self._relay()
            .table("RemoteToolCalls")
            .select("id,status")
            .in_("id", ids)
            .execute()
        ).data or []
        return {r["id"]: r["status"] for r in rows}

    def stop_conversation(self, conversation_id: str) -> None:
        self.client.rpc(
            "stop_conversation",
            {
                "_conversation_id": conversation_id,
                "_account_id": self.account_id,
            },
        ).execute()

    def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        return (
            self.client.table("Conversations")
            .select("*")
            .eq("conversation_id", conversation_id)
            .single()
            .execute()
        ).data

    def get_events(self, conversation_id: str) -> List[Dict[str, Any]]:
        # Direct table read (not the get_conversation_events RPC used in
        # production) because compaction assertions need the per-row
        # ``compacted`` flag, which the RPC's flattened result set hides.
        # Works under RLS for the logged-in test user.
        return (
            self.client.table("ConversationEvents")
            .select("*")
            .eq("conversation_id", conversation_id)
            .order("seq")
            .execute()
        ).data or []

    def flat_event_types(self, conversation_id: str) -> List[str]:
        """Return a flat list of event type strings across all rows."""
        types = []
        for row in self.get_events(conversation_id):
            for ev in row.get("events") or []:
                types.append(ev.get("event"))
        return types

    def wait_for_status(
        self,
        conversation_id: str,
        target_statuses: set,
        timeout: float = 120,
        poll_interval: float = 1.0,
    ) -> Dict[str, Any]:
        """Poll until the conversation reaches one of the target statuses."""
        start = time.time()
        while time.time() - start < timeout:
            conv = self.get_conversation(conversation_id)
            if conv["status"] in target_statuses:
                return conv
            time.sleep(poll_interval)
        conv = self.get_conversation(conversation_id)
        raise TimeoutError(
            f"Conversation {conversation_id} did not reach {target_statuses} "
            f"within {timeout}s (current: {conv['status']})"
        )

    def wait_for_terminal(
        self,
        conversation_id: str,
        request_sequence: int,
        timeout: float = 120,
    ) -> Dict[str, Any]:
        """Wait until conversation is terminal for the given request_sequence."""
        start = time.time()
        while time.time() - start < timeout:
            conv = self.get_conversation(conversation_id)
            if (
                conv["request_sequence"] == request_sequence
                and conv["status"] in ("completed", "failed", "stopped")
            ):
                return conv
            time.sleep(1.0)
        conv = self.get_conversation(conversation_id)
        raise TimeoutError(
            f"Conversation {conversation_id} not terminal for seq={request_sequence} "
            f"within {timeout}s (status={conv['status']}, seq={conv['request_sequence']})"
        )

    def get_compaction_stats(self, conversation_id: str) -> Dict[str, Any]:
        """Return compaction statistics for the conversation's event rows."""
        rows = self.get_events(conversation_id)
        compacted = [r for r in rows if r.get("compacted")]
        non_compacted = [r for r in rows if not r.get("compacted")]
        return {
            "total": len(rows),
            "compacted": len(compacted),
            "non_compacted": len(non_compacted),
            "compacted_seqs": [r["seq"] for r in compacted],
            "non_compacted_seqs": [r["seq"] for r in non_compacted],
        }

    def find_terminal_event(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Find the last terminal event (ai_answer_end / approval_required / error)."""
        for row in reversed(self.get_events(conversation_id)):
            for ev in reversed(row.get("events") or []):
                if ev.get("event") in ("ai_answer_end", "approval_required", "error"):
                    return ev
        return None


@pytest.fixture(scope="session")
def supabase_fx(request) -> SupabaseFixture:
    """Session-scoped Supabase client fixture.

    Requires ROBUSTA_UI_TOKEN and CLUSTER_NAME environment variables.
    Performs best-effort cleanup of created conversations after the session,
    unless ``--skip-cleanup`` is passed (useful for inspecting the rows that
    a test left behind in the DB).
    """
    decoded = _decode_token()
    cluster_id = os.environ.get("CLUSTER_NAME")
    if not cluster_id:
        pytest.skip("CLUSTER_NAME not set")

    options = ClientOptions(postgrest_client_timeout=60)
    client = create_client(decoded["store_url"], decoded["api_key"], options)
    res = client.auth.sign_in_with_password(
        {"email": decoded["email"], "password": decoded["password"]}
    )
    client.auth.set_session(res.session.access_token, res.session.refresh_token)
    client.postgrest.auth(res.session.access_token)

    use_broadcast_str = os.environ.get("CONVERSATION_WORKER_USE_REALTIME_BROADCAST", "true")
    use_broadcast = use_broadcast_str.lower() in ("true", "1", "yes")
    use_pgchanges = not use_broadcast

    fx = SupabaseFixture(
        client=client,
        account_id=decoded["account_id"],
        cluster_id=cluster_id,
        user_id=res.user.id,
        _store_url=decoded["store_url"],
        _api_key=decoded["api_key"],
        use_pgchanges=use_pgchanges,
    )
    yield fx

    if request.config.getoption("--skip-cleanup"):
        if fx._created_conversations:
            logging.warning(
                "--skip-cleanup set: leaving %d conversation(s) in the DB: %s",
                len(fx._created_conversations),
                fx._created_conversations,
            )
        return

    # Best-effort teardown: stop any still-active conversations and delete them
    for cid in fx._created_conversations:
        try:
            conv = fx.get_conversation(cid)
            if conv["status"] in ("pending", "queued", "running"):
                fx.stop_conversation(cid)
        except Exception:
            logging.warning(
                "Failed to stop conversation %s during teardown",
                cid,
                exc_info=True,
            )
        try:
            client.table("ConversationEvents").delete().eq(
                "conversation_id", cid
            ).execute()
            client.table("Conversations").delete().eq(
                "conversation_id", cid
            ).execute()
        except Exception:
            logging.warning(
                "Failed to delete conversation %s during teardown",
                cid,
                exc_info=True,
            )

    # Best-effort cleanup of RemoteToolCalls rows. The table has no DELETE RLS
    # policy (rows normally leave only via the retention job), so this may be a
    # no-op — that's fine for the isolated test cluster. Quiet on failure.
    if fx._created_tool_calls and fx._relay_client is not None:
        for tcid in fx._created_tool_calls:
            try:
                fx._relay_client.table("RemoteToolCalls").delete().eq(
                    "id", tcid
                ).execute()
            except Exception:  # noqa: BLE001 - best-effort teardown cleanup
                logging.debug(
                    "Could not delete remote tool call %s during teardown "
                    "(expected: no DELETE policy)",
                    tcid,
                )
