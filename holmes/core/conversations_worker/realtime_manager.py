"""
Realtime manager for the ConversationWorker.

Runs an asyncio event loop in a background daemon thread. Manages a Supabase
Realtime subscription that notifies the worker when new pending conversations
appear.  Two subscription modes are supported (selected via the
``CONVERSATION_WORKER_USE_REALTIME_BROADCAST`` env var):

 1. **Postgres Changes** — subscribes to INSERT/UPDATE on the
    Conversations table filtered by ``account_id``.
 2. **Broadcast** (default) — subscribes to a per-account-per-cluster Broadcast channel
    ``holmes:submit:{account_id}:{cluster_id}``.  The initiator (Frontend /
    Relay) must send a broadcast after creating the conversation.

Communication with the sync ConversationWorker is via a callback that is
invoked when a pending-conversation notification arrives. The callback MUST
be thread-safe (the worker passes a threading.Event.set).
"""
from __future__ import annotations

import asyncio
import logging
import os
import ssl
import threading
import urllib.parse
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

import realtime._async.client as rt_client
from realtime._async.channel import ChannelStates
from realtime._async.client import AsyncRealtimeClient

from holmes.common.env_vars import (
    CONVERSATION_WORKER_AUTH_REFRESH_INTERVAL_SECONDS,
    CONVERSATION_WORKER_REALTIME_HEALTH_TICK_SECONDS,
    CONVERSATION_WORKER_REALTIME_RECONNECT_MAX_SECONDS,
    CONVERSATION_WORKER_USE_REALTIME_BROADCAST,
)
from holmes.core.supabase_dal import CONVERSATIONS_TABLE

if TYPE_CHECKING:
    from holmes.core.supabase_dal import SupabaseDal


# ---- channel topic helpers ----


def pg_changes_topic(account_id: str) -> str:
    """Per-account channel for Conversations Postgres Changes."""
    return f"holmes:pgchanges:{account_id}"


def broadcast_submit_topic(account_id: str, cluster_id: str) -> str:
    """Per-account-per-cluster Broadcast channel for conversation submissions.

    No WAL replication overhead — the initiator sends a broadcast message
    after creating the conversation via RPC.
    """
    return f"holmes:submit:{account_id}:{cluster_id}"


def _build_ssl_context() -> ssl.SSLContext:
    """Build the SSL context used for outbound Realtime WebSocket connections.

    The ``CERTIFICATE`` env var (handled by ``holmes.utils.cert_utils``) sets
    ``REQUESTS_CA_BUNDLE`` / ``WEBSOCKET_CLIENT_CA_BUNDLE`` and patches
    ``certifi.where()``, but the ``websockets`` stdlib client ignores all of
    those and falls back to the OS trust store — so a custom CA never makes
    it into the WS handshake. Honor the env vars here so the realtime
    connection trusts the same bundle the rest of the app does.
    """
    cafile = (
        os.environ.get("REQUESTS_CA_BUNDLE")
        or os.environ.get("WEBSOCKET_CLIENT_CA_BUNDLE")
    )
    if cafile:
        if os.path.exists(cafile):
            return ssl.create_default_context(cafile=cafile)
        logging.warning(
            "CA bundle %s does not exist; falling back to OS trust store",
            cafile,
        )
    return ssl.create_default_context()


def _install_ssl_patch_if_needed() -> None:
    """
    Monkey-patch ``realtime._async.client.connect`` to inject an SSL context
    that trusts the custom CA bundle (``CERTIFICATE`` env var) for ``wss://``
    targets. The websockets stdlib client otherwise uses only the OS trust
    store, breaking deployments behind a corporate / private CA.

    HTTP CONNECT proxy support (``https_proxy`` / ``HTTPS_PROXY`` env vars) is
    handled natively by ``websockets`` ≥ 13 via ``python-socks``, so no proxy
    monkey-patching is needed — websockets reads the env var itself when its
    ``connect()`` is called with no explicit ``proxy`` kwarg, and our patch
    leaves all other kwargs untouched.

    Idempotent.
    """
    cafile = (
        os.environ.get("REQUESTS_CA_BUNDLE")
        or os.environ.get("WEBSOCKET_CLIENT_CA_BUNDLE")
    )
    if not cafile:
        return
    if not os.path.exists(cafile):
        logging.warning(
            "CA bundle %s does not exist; falling back to OS trust store",
            cafile,
        )
        return

    if getattr(rt_client, "_holmes_ssl_patched", False):
        return

    ctx = _build_ssl_context()
    original_connect = rt_client.connect

    async def _ssl_connect(url: str, *args: Any, **kwargs: Any) -> Any:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == "wss" and "ssl" not in kwargs:
            kwargs["ssl"] = ctx
        return await original_connect(url, *args, **kwargs)

    rt_client.connect = _ssl_connect  # type: ignore[attr-defined]
    rt_client._holmes_ssl_patched = True  # type: ignore[attr-defined]
    logging.info(
        "Installed WebSocket SSL patch for realtime client (cafile=%s)", cafile
    )


class RealtimeManager:
    def __init__(
        self,
        dal: "SupabaseDal",
        holmes_id: str,
        on_new_pending: Callable[[], None],
        use_broadcast: bool = CONVERSATION_WORKER_USE_REALTIME_BROADCAST,
    ) -> None:
        self.dal = dal
        self.holmes_id = holmes_id
        self.on_new_pending = on_new_pending
        self._use_broadcast = use_broadcast
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started = threading.Event()
        self._client = None
        self._channel = None
        # True once the subscription channel is SUBSCRIBED (drives
        # is_connected() and the claim-loop's realtime-vs-poll decision).
        self._connected = False
        # Last JWT we pushed to the realtime client via set_auth.
        self._last_auth_jwt: Optional[str] = None
        # Set from the async loop to wake the sleep in _run() on stop().
        self._async_stop: Optional[asyncio.Event] = None

    # ---- public ----

    def is_connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._started.clear()
        self._loop = None
        self._client = None
        self._channel = None
        self._connected = False
        self._last_auth_jwt = None
        self._async_stop = None
        self._thread = threading.Thread(
            target=self._thread_entry,
            daemon=True,
            name="realtime-manager",
        )
        self._thread.start()
        self._started.wait(timeout=5)

    def stop(self) -> None:
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            try:
                # Close the realtime client first and *wait* for it to finish —
                # otherwise the fire-and-forget coro races with _run()'s exit
                # and asyncio.run() will cancel _shutdown_async() mid-close,
                # leaking the WebSocket.
                future = asyncio.run_coroutine_threadsafe(
                    self._shutdown_async(), self._loop
                )
                try:
                    future.result(timeout=5)
                except Exception:
                    logging.exception(
                        "Error waiting for realtime shutdown", exc_info=True
                    )
                # Then wake the async sleep so _run() exits promptly instead
                # of blocking for up to the refresh interval.
                if self._async_stop is not None:
                    self._loop.call_soon_threadsafe(self._async_stop.set)
            except Exception:
                logging.exception("Error scheduling shutdown coro", exc_info=True)
        if self._thread:
            self._thread.join(timeout=5)

    # ---- thread entry point ----

    def _thread_entry(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception:
            logging.exception("Realtime manager thread crashed", exc_info=True)

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._async_stop = asyncio.Event()
        self._started.set()
        reconnect_attempts = 0
        max_backoff = CONVERSATION_WORKER_REALTIME_RECONNECT_MAX_SECONDS
        try:
            # Initial connect uses the same backoff as mid-run reconnects
            # so transient startup failures (e.g. Supabase 503) are retried
            # instead of killing the thread.
            while not self._stop_event.is_set():
                success = await self._full_reconnect()
                if success:
                    reconnect_attempts = 0
                    break
                reconnect_attempts += 1
                backoff = min(max_backoff, 2 ** reconnect_attempts)
                logging.warning(
                    "Initial connect failed (attempt %d), retrying in %ds",
                    reconnect_attempts,
                    backoff,
                )
                try:
                    await asyncio.wait_for(
                        self._async_stop.wait(), timeout=backoff
                    )
                    return  # _async_stop set → stop() was called
                except asyncio.TimeoutError:
                    pass

            if self._stop_event.is_set():
                return

            refresh_interval = CONVERSATION_WORKER_AUTH_REFRESH_INTERVAL_SECONDS
            health_tick = CONVERSATION_WORKER_REALTIME_HEALTH_TICK_SECONDS
            next_refresh_at = asyncio.get_running_loop().time() + refresh_interval
            while not self._stop_event.is_set():
                now = asyncio.get_running_loop().time()

                # Detect channel closure or silently-dead WS. The library's
                # auto-reconnect is unreliable on clean closes, so we do our
                # own full teardown/reconnect on any failure signal.
                unhealthy_reason = self._channel_unhealthy()
                if unhealthy_reason is not None:
                    logging.warning(
                        "Realtime channel unhealthy (%s), reconnecting",
                        unhealthy_reason,
                    )
                    self._connected = False
                    try:
                        self.on_new_pending()
                    except Exception:
                        logging.debug(
                            "on_new_pending failed during reconnect",
                            exc_info=True,
                        )
                    success = await self._full_reconnect()
                    if success:
                        reconnect_attempts = 0
                        next_refresh_at = (
                            asyncio.get_running_loop().time() + refresh_interval
                        )
                    else:
                        reconnect_attempts += 1
                        backoff = min(max_backoff, 2 ** reconnect_attempts)
                        logging.warning(
                            "Reconnect failed (attempt %d), backing off %ds",
                            reconnect_attempts,
                            backoff,
                        )
                        try:
                            await asyncio.wait_for(
                                self._async_stop.wait(), timeout=backoff
                            )
                            break  # stop() was called
                        except asyncio.TimeoutError:
                            pass
                        next_refresh_at = (
                            asyncio.get_running_loop().time() + refresh_interval
                        )
                    continue

                if now >= next_refresh_at:
                    await self._maybe_refresh_auth()
                    next_refresh_at = (
                        asyncio.get_running_loop().time() + refresh_interval
                    )
                # Cap the sleep at the health tick so a silently-dead WS is
                # detected within ~health_tick seconds rather than waiting
                # for the next auth refresh.
                now = asyncio.get_running_loop().time()
                sleep_for = max(
                    0.01,
                    min(next_refresh_at - now, health_tick),
                )
                # wait_for with _async_stop allows stop() to wake us
                # immediately via call_soon_threadsafe instead of blocking
                # for the full sleep interval.
                try:
                    await asyncio.wait_for(
                        self._async_stop.wait(), timeout=sleep_for
                    )
                    break  # _async_stop was set → exit loop
                except asyncio.TimeoutError:
                    pass  # normal wake — re-check health and refresh
        except Exception:
            logging.exception("Error in realtime manager main loop", exc_info=True)
        finally:
            self._connected = False
            try:
                await self._shutdown_async()
            except Exception:
                logging.debug(
                    "Error closing client during _run exit", exc_info=True
                )
            try:
                self.on_new_pending()
            except Exception:
                logging.debug(
                    "on_new_pending callback failed during shutdown",
                    exc_info=True,
                )

    def _channel_unhealthy(self) -> Optional[str]:
        """Return a short reason string when the subscription is unhealthy, else None.

        Detects the silent-death window in the realtime library: when the
        server closes the WS cleanly (ConnectionClosedOK), `_listen` exits
        without triggering auto-reconnect, the heartbeat coroutine swallows
        the close, and `is_connected` keeps returning True. The channel
        state also stays JOINED because no `phx_close` arrives.

        Strongest signals (in order of reliability):
          1. listen task done — cleanest indicator of a dead read loop
          2. heartbeat task done — write loop crashed/exited
          3. ws_connection cleared — auto-reconnect did fire but failed
          4. channel state != JOINED — server-side close (token expiry, etc.)
        """
        if self._channel is None:
            return "channel_none"
        if self._channel.state != ChannelStates.JOINED:
            return f"channel_state={self._channel.state}"
        if self._client is None:
            return "client_none"
        if not self._client.is_connected:
            return "ws_disconnected"
        # Library internals — guarded so a future rename degrades to the
        # public-API checks above instead of crashing the worker.
        listen_task = getattr(self._client, "_listen_task", None)
        if listen_task is None or listen_task.done():
            return "listen_task_done"
        heartbeat_task = getattr(self._client, "_heartbeat_task", None)
        if heartbeat_task is None or heartbeat_task.done():
            return "heartbeat_task_done"
        return None

    async def _full_reconnect(self) -> bool:
        """Tear down the current client and re-establish from scratch.

        Forces a fresh ``sign_in()`` first — ``get_session()`` has not
        proven reliable at auto-refreshing when the only active consumer
        is the realtime WebSocket (no postgrest queries to trigger the
        Supabase client's internal refresh path).  The DAL uses the same
        re-sign-in pattern on PGRST301 / JWT-expired errors.

        Returns True on success, False on failure.
        """
        try:
            if self._client:
                await self._client.close()
        except Exception:
            logging.debug("Error closing client during reconnect", exc_info=True)
        self._client = None
        self._channel = None
        self._last_auth_jwt = None
        try:
            await asyncio.to_thread(self.dal.sign_in)
        except Exception:
            logging.exception(
                "Failed to re-sign-in to Supabase before reconnect",
                exc_info=True,
            )
            return False
        try:
            await self._connect_and_subscribe()
            return True
        except Exception:
            logging.exception("Failed to reconnect", exc_info=True)
            return False

    async def _maybe_refresh_auth(self) -> None:
        """Re-push the Supabase JWT to the realtime client if it rotated."""
        if not self._client:
            return
        try:
            session = self.dal.client.auth.get_session()  # type: ignore[attr-defined]
            if session is None:
                return
            new_jwt = session.access_token
            if not new_jwt or new_jwt == self._last_auth_jwt:
                return
            await self._client.set_auth(new_jwt)
            self._last_auth_jwt = new_jwt
            logging.debug("Refreshed realtime client auth token")
        except Exception:
            logging.exception("Failed to refresh realtime auth token", exc_info=True)

    # ---- connect + subscribe ----

    async def _connect_and_subscribe(self) -> None:
        # HTTP CONNECT proxy (https_proxy / HTTPS_PROXY env vars) is handled
        # natively by websockets ≥ 13 via python-socks — no monkey-patching
        # needed for transport. The SSL patch is still required because the
        # realtime library calls connect(self.url) without an ssl= kwarg, so
        # we have to inject one to honor the custom CA bundle.
        _install_ssl_patch_if_needed()

        # Supabase Realtime URL
        store_url = self.dal.url.rstrip("/")
        if store_url.startswith("https://"):
            ws_url = "wss://" + store_url[len("https://"):]
        elif store_url.startswith("http://"):
            ws_url = "ws://" + store_url[len("http://"):]
        else:
            ws_url = store_url
        ws_url = f"{ws_url}/realtime/v1"

        apikey = self.dal.api_key
        session = self.dal.client.auth.get_session()  # type: ignore[attr-defined]
        user_jwt = session.access_token if session else None
        if not user_jwt:
            logging.warning(
                "No Supabase session available during realtime connect; "
                "RLS-scoped subscriptions may not work until a token refresh"
            )

        self._client = AsyncRealtimeClient(
            url=ws_url,
            token=apikey,
            auto_reconnect=True,
        )
        try:
            await self._client.connect()
            if user_jwt:
                try:
                    await self._client.set_auth(user_jwt)
                    self._last_auth_jwt = user_jwt
                except Exception:
                    logging.exception(
                        "Failed to set_auth on realtime client", exc_info=True
                    )

            # Subscribe using the configured mode.
            if self._use_broadcast:
                await self._subscribe_via_broadcast()
            else:
                await self._subscribe_via_pgchanges()
        except Exception:
            # Close any partially-open client/socket so we don't leak it when
            # the loop unwinds past this failure.
            try:
                await self._client.close()
            except Exception:
                logging.exception(
                    "Error closing realtime client after failed connect",
                    exc_info=True,
                )
            self._client = None
            raise

    async def _subscribe_via_pgchanges(self) -> None:
        """Option 1: Postgres Changes on the Conversations table.

        Subscribes to INSERT/UPDATE filtered by ``account_id``.  Every
        Conversations row change triggers a claim attempt.
        """
        topic = pg_changes_topic(self.dal.account_id)
        self._channel = self._client.channel(
            topic,
            {"config": {"private": True, "presence": {"enabled": False}}},
        )

        def _on_pg_change(payload: Dict[str, Any]) -> None:
            try:
                change = payload.get("data", {}) or {}
                logging.info(
                    "RealtimeManager: Postgres change notification: %s",
                    change.get("type"),
                )
                self.on_new_pending()
            except Exception:
                logging.exception("Error in realtime pg change callback", exc_info=True)

        account_id_filter = f"account_id=eq.{self.dal.account_id}"
        self._channel.on_postgres_changes(
            event="INSERT",
            schema="public",
            table=CONVERSATIONS_TABLE,
            filter=account_id_filter,
            callback=_on_pg_change,
        )
        self._channel.on_postgres_changes(
            event="UPDATE",
            schema="public",
            table=CONVERSATIONS_TABLE,
            filter=account_id_filter,
            callback=_on_pg_change,
        )

        subscribed = asyncio.Event()

        def _on_subscribe(status: Any, err: Optional[Exception] = None) -> None:
            logging.info("PG changes subscribe status=%s err=%s", status, err)
            status_str = str(status).upper()
            if "SUBSCRIBED" in status_str:
                self._connected = True
                subscribed.set()
                try:
                    self.on_new_pending()
                except Exception:
                    logging.debug(
                        "on_new_pending callback failed in pg subscribe",
                        exc_info=True,
                    )
            elif any(
                s in status_str for s in ("CHANNEL_ERROR", "CLOSED", "TIMED_OUT")
            ):
                self._connected = False
                subscribed.set()
                try:
                    self.on_new_pending()
                except Exception:
                    logging.debug(
                        "on_new_pending callback failed in pg error handler",
                        exc_info=True,
                    )

        await self._channel.subscribe(_on_subscribe)
        try:
            await asyncio.wait_for(subscribed.wait(), timeout=5)
        except asyncio.TimeoutError:
            logging.warning("Timed out waiting for pg-changes subscribe ack")

        logging.info("RealtimeManager connected: mode=pgchanges topic=%s", topic)

    async def _subscribe_via_broadcast(self) -> None:
        """Option 2: Broadcast channel per account + cluster.

        Subscribes to ``holmes:submit:{account_id}:{cluster_id}``.  The
        initiator sends a broadcast after creating the conversation via RPC.
        No WAL replication overhead — the message goes directly through the
        Realtime WebSocket.
        """
        topic = broadcast_submit_topic(self.dal.account_id, self.dal.cluster)
        self._channel = self._client.channel(
            topic,
            {"config": {"private": True, "presence": {"enabled": False}}},
        )

        def _on_broadcast(payload: Dict[str, Any]) -> None:
            try:
                logging.info(
                    "RealtimeManager: Broadcast notification: %s",
                    payload.get("event"),
                )
                self.on_new_pending()
            except Exception:
                logging.exception("Error in broadcast callback", exc_info=True)

        # Event name acts as the submission-type discriminator so the channel
        # can be reused for future submission types (e.g. tool approval
        # responses, cancellations) without collision.
        self._channel.on_broadcast(
            event="pending_conversations",
            callback=_on_broadcast,
        )

        subscribed = asyncio.Event()

        def _on_subscribe(status: Any, err: Optional[Exception] = None) -> None:
            logging.info("Broadcast subscribe status=%s err=%s", status, err)
            status_str = str(status).upper()
            if "SUBSCRIBED" in status_str:
                self._connected = True
                subscribed.set()
                try:
                    self.on_new_pending()
                except Exception:
                    logging.debug(
                        "on_new_pending callback failed in broadcast subscribe",
                        exc_info=True,
                    )
            elif any(
                s in status_str for s in ("CHANNEL_ERROR", "CLOSED", "TIMED_OUT")
            ):
                self._connected = False
                subscribed.set()
                try:
                    self.on_new_pending()
                except Exception:
                    logging.debug(
                        "on_new_pending callback failed in broadcast error handler",
                        exc_info=True,
                    )

        await self._channel.subscribe(_on_subscribe)
        try:
            await asyncio.wait_for(subscribed.wait(), timeout=5)
        except asyncio.TimeoutError:
            logging.warning("Timed out waiting for broadcast subscribe ack")

        logging.info("RealtimeManager connected: mode=broadcast topic=%s", topic)

    async def _shutdown_async(self) -> None:
        self._connected = False
        try:
            if self._client:
                await self._client.close()
        except Exception:
            logging.exception("Error shutting down realtime client", exc_info=True)
