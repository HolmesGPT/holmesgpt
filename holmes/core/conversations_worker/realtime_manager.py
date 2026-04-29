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
from websockets.asyncio.client import connect as ws_connect

try:
    # python-socks is only required when an HTTP CONNECT proxy is in use
    # (sandboxed environments, enterprise egress). Normal deployments don't
    # need it.
    from python_socks.async_.asyncio import Proxy as _SocksProxy
except ImportError:  # pragma: no cover — optional dependency
    _SocksProxy = None  # type: ignore[assignment]

from holmes.common.env_vars import (
    CONVERSATION_WORKER_AUTH_REFRESH_INTERVAL_SECONDS,
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


def _install_proxy_patch_if_needed() -> None:
    """
    If an ``https_proxy`` env var is set, monkey-patch ``realtime._async.client.connect``
    so the WebSocket connection is tunneled through the HTTP CONNECT proxy. This
    is needed in sandboxed environments that require all egress to go through a
    proxy (and direct DNS/TCP are blocked).

    Idempotent — only patches once.
    """
    proxy_url = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    if not proxy_url:
        return

    if getattr(rt_client, "_holmes_proxy_patched", False):
        return

    if _SocksProxy is None:
        logging.warning(
            "https_proxy is set but python-socks is not installed; "
            "Realtime WebSocket will attempt direct connection and likely fail. "
            "Install python-socks to tunnel WS through the proxy."
        )
        return

    p = urllib.parse.urlparse(proxy_url)
    if not p.hostname:
        logging.warning("https_proxy has no hostname; skipping proxy patch")
        return
    proxy_connect_url = f"http://{p.hostname}"
    if p.username and p.password:
        proxy_connect_url = f"http://{p.username}:{p.password}@{p.hostname}"
    elif p.username:
        proxy_connect_url = f"http://{p.username}@{p.hostname}"
    if p.port is not None:
        proxy_connect_url += f":{p.port}"

    async def _proxied_connect(url: str, *args: Any, **kwargs: Any) -> Any:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("ws", "wss"):
            return await ws_connect(url, *args, **kwargs)

        # skip proxy for localhost targets
        if parsed.hostname in ("localhost", "127.0.0.1"):
            return await ws_connect(url, *args, **kwargs)

        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        proxy = _SocksProxy.from_url(proxy_connect_url)
        sock = await proxy.connect(dest_host=parsed.hostname, dest_port=port)
        kwargs.setdefault("server_hostname", parsed.hostname)
        if parsed.scheme == "wss" and "ssl" not in kwargs:
            kwargs["ssl"] = ssl.create_default_context()
        return await ws_connect(url, *args, sock=sock, **kwargs)

    rt_client.connect = _proxied_connect  # type: ignore[attr-defined]
    rt_client._holmes_proxy_patched = True  # type: ignore[attr-defined]
    logging.info(
        "Installed WebSocket proxy patch for realtime client (proxy=%s:%s)",
        p.hostname,
        p.port,
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
            next_refresh_at = asyncio.get_running_loop().time() + refresh_interval
            while not self._stop_event.is_set():
                now = asyncio.get_running_loop().time()

                # Detect channel closure (e.g. token expiry → server sends
                # phx_close). The library's auto-reconnect is unreliable
                # after a channel close, so we do a full teardown/reconnect.
                if await self._channel_needs_reconnect():
                    logging.warning(
                        "Channel no longer joined (state=%s), reconnecting",
                        self._channel.state if self._channel else "None",
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
                sleep_for = max(
                    0.01,
                    next_refresh_at - asyncio.get_running_loop().time(),
                )
                # wait_for with _async_stop allows stop() to wake us
                # immediately via call_soon_threadsafe instead of blocking
                # for the full refresh interval.
                try:
                    await asyncio.wait_for(
                        self._async_stop.wait(), timeout=sleep_for
                    )
                    break  # _async_stop was set → exit loop
                except asyncio.TimeoutError:
                    pass  # normal wake — check refresh and loop
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

    async def _channel_needs_reconnect(self) -> bool:
        """True when the subscription channel has left the JOINED state.

        Supabase closes the channel on JWT expiry ("Token has expired").
        The library's built-in auto-reconnect is unreliable after a channel
        close — the _listen task can crash with ``ValueError('Set of
        Tasks/Futures is empty.')``.  We detect this early and do our own
        full reconnect.
        """
        if self._channel is None:
            return True
        return self._channel.state != ChannelStates.JOINED

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
        _install_proxy_patch_if_needed()

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
            {"config": {"private": False}},
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
            {"config": {"private": False}},
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
