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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from realtime._async.client import AsyncRealtimeClient
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

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
    ) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        user_msg_data: Dict[str, Any] = {"ask": ask}
        if enable_tool_approval:
            user_msg_data["enable_tool_approval"] = True
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
                ch = rt.channel(topic, {"config": {"private": False}})
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
def supabase_fx() -> SupabaseFixture:
    """Session-scoped Supabase client fixture.

    Requires ROBUSTA_UI_TOKEN and CLUSTER_NAME environment variables.
    Performs best-effort cleanup of created conversations after the session.
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
