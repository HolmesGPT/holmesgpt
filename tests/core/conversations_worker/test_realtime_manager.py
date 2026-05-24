"""Unit tests for RealtimeManager's testable (non-async) surface."""
import asyncio
import logging
import os
import ssl as _ssl
from unittest.mock import MagicMock

import certifi
import pytest
import realtime._async.client as rt_client
from postgrest.exceptions import APIError as PGAPIError
from realtime._async.channel import ChannelStates
from websockets.exceptions import WebSocketException

from holmes.core.conversations_worker.realtime_manager import (
    RealtimeManager,
    _build_ssl_context,
    _install_realtime_log_filter_if_needed,
    _install_ssl_patch_if_needed,
    _RealtimeConnectivityWarningFilter,
    _TRANSIENT_RECONNECT_EXCEPTIONS,
    broadcast_submit_topic,
    pg_changes_topic,
)
from holmes.core.supabase_dal import SupabaseDnsException


def _make_manager():
    dal = MagicMock()
    dal.url = "https://sp.stg.example"
    dal.account_id = "acc-1"
    dal.cluster = "cluster-1"
    return RealtimeManager(dal=dal, holmes_id="h-test", on_new_pending=MagicMock())


def test_initial_state_is_disconnected():
    m = _make_manager()
    assert m.is_connected() is False


def test_is_connected_reflects_connection_flag():
    m = _make_manager()
    m._connected = True
    assert m.is_connected() is True
    m._connected = False
    assert m.is_connected() is False


def test_topic_helpers():
    assert pg_changes_topic("acc-1") == "holmes:pgchanges:acc-1"
    assert (
        broadcast_submit_topic("acc-1", "cluster-1")
        == "holmes:submit:acc-1:cluster-1"
    )


# ---- SSL / custom CA patching ----


def test_install_ssl_patch_does_nothing_without_ca_bundle(monkeypatch):
    """No CA env var → no patch."""
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("WEBSOCKET_CLIENT_CA_BUNDLE", raising=False)

    rt_client._holmes_ssl_patched = False
    original_connect = rt_client.connect
    _install_ssl_patch_if_needed()
    assert rt_client.connect is original_connect
    assert not getattr(rt_client, "_holmes_ssl_patched", False)


def test_install_ssl_patch_does_nothing_when_ca_bundle_missing(
    monkeypatch, tmp_path
):
    """CA env var pointing at a non-existent path is a no-op (don't crash)."""
    monkeypatch.setenv(
        "REQUESTS_CA_BUNDLE", str(tmp_path / "does-not-exist.pem")
    )
    monkeypatch.delenv("WEBSOCKET_CLIENT_CA_BUNDLE", raising=False)

    rt_client._holmes_ssl_patched = False
    original_connect = rt_client.connect
    _install_ssl_patch_if_needed()
    assert rt_client.connect is original_connect
    assert not getattr(rt_client, "_holmes_ssl_patched", False)


def test_install_ssl_patch_injects_ssl_for_wss(monkeypatch, tmp_path):
    """When a CA bundle is configured, wss:// connects must get ssl kwarg."""
    # Use the system certifi bundle as our "custom CA" — it's a real,
    # parseable PEM file, which is all create_default_context(cafile=...) needs.
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", certifi.where())
    monkeypatch.delenv("WEBSOCKET_CLIENT_CA_BUNDLE", raising=False)

    rt_client._holmes_ssl_patched = False
    original_connect = rt_client.connect

    captured_kwargs = {}

    async def fake_connect(url, *args, **kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    rt_client.connect = fake_connect

    try:
        _install_ssl_patch_if_needed()
        assert getattr(rt_client, "_holmes_ssl_patched", False) is True

        asyncio.run(rt_client.connect("wss://realtime.example/realtime/v1"))
        assert "ssl" in captured_kwargs, "wss:// must get an ssl context"
        assert isinstance(captured_kwargs["ssl"], _ssl.SSLContext)
    finally:
        rt_client.connect = original_connect
        rt_client._holmes_ssl_patched = False


def test_install_ssl_patch_does_not_clobber_existing_ssl(monkeypatch):
    """Caller-supplied ssl kwarg must win — don't overwrite proxy patch's ctx."""
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", certifi.where())

    rt_client._holmes_ssl_patched = False
    original_connect = rt_client.connect

    captured_kwargs = {}

    async def fake_connect(url, *args, **kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    rt_client.connect = fake_connect
    sentinel_ctx = _ssl.create_default_context()

    try:
        _install_ssl_patch_if_needed()
        asyncio.run(
            rt_client.connect(
                "wss://realtime.example/realtime/v1", ssl=sentinel_ctx
            )
        )
        assert captured_kwargs["ssl"] is sentinel_ctx
    finally:
        rt_client.connect = original_connect
        rt_client._holmes_ssl_patched = False


def test_install_ssl_patch_skips_non_wss(monkeypatch):
    """Plain ws:// (or non-WS schemes) must not get a forced ssl kwarg."""
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", certifi.where())

    rt_client._holmes_ssl_patched = False
    original_connect = rt_client.connect

    captured_kwargs = {}

    async def fake_connect(url, *args, **kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    rt_client.connect = fake_connect

    try:
        _install_ssl_patch_if_needed()
        asyncio.run(rt_client.connect("ws://localhost:54321/realtime/v1"))
        assert "ssl" not in captured_kwargs
    finally:
        rt_client.connect = original_connect
        rt_client._holmes_ssl_patched = False


def test_install_ssl_patch_is_idempotent(monkeypatch):
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", certifi.where())

    rt_client._holmes_ssl_patched = False
    original_connect = rt_client.connect
    try:
        _install_ssl_patch_if_needed()
        first_patched = rt_client.connect
        assert first_patched is not original_connect

        _install_ssl_patch_if_needed()
        assert rt_client.connect is first_patched
    finally:
        rt_client.connect = original_connect
        rt_client._holmes_ssl_patched = False


def test_build_ssl_context_uses_custom_ca(monkeypatch):
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", certifi.where())
    ctx = _build_ssl_context()
    assert isinstance(ctx, _ssl.SSLContext)
    # Default context verifies the cert chain — if the cafile didn't load,
    # SSLContext construction wouldn't have raised, but we'd be back on the
    # OS store. Sanity-check via verify_mode.
    assert ctx.verify_mode == _ssl.CERT_REQUIRED


def test_build_ssl_context_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("WEBSOCKET_CLIENT_CA_BUNDLE", raising=False)
    ctx = _build_ssl_context()
    assert isinstance(ctx, _ssl.SSLContext)


# ---- _channel_unhealthy ----


def _alive_task():
    """Return a not-done asyncio.Task whose .done() == False."""
    t = MagicMock()
    t.done.return_value = False
    return t


def _done_task():
    t = MagicMock()
    t.done.return_value = True
    return t


def _make_healthy_manager():
    m = _make_manager()
    m._channel = MagicMock()
    m._channel.state = ChannelStates.JOINED
    m._client = MagicMock()
    m._client.is_connected = True
    m._client._listen_task = _alive_task()
    m._client._heartbeat_task = _alive_task()
    return m


def test_unhealthy_when_channel_none():
    m = _make_manager()
    assert m._channel_unhealthy() == "channel_none"


def test_unhealthy_when_channel_not_joined():
    m = _make_healthy_manager()
    m._channel.state = ChannelStates.CLOSED
    reason = m._channel_unhealthy()
    assert reason is not None and reason.startswith("channel_state=")


def test_unhealthy_when_client_none():
    m = _make_healthy_manager()
    m._client = None
    assert m._channel_unhealthy() == "client_none"


def test_unhealthy_when_ws_disconnected():
    m = _make_healthy_manager()
    m._client.is_connected = False
    assert m._channel_unhealthy() == "ws_disconnected"


def test_unhealthy_when_listen_task_done():
    """Silent-death case: listen task exited cleanly on ConnectionClosedOK.

    is_connected stays True, channel state stays JOINED, but the listen task
    is done — the read loop is gone and no notifications will arrive. This
    is the production failure mode the in-loop health check exists to catch.
    """
    m = _make_healthy_manager()
    m._client._listen_task = _done_task()
    assert m._channel_unhealthy() == "listen_task_done"


def test_unhealthy_when_listen_task_missing():
    m = _make_healthy_manager()
    m._client._listen_task = None
    assert m._channel_unhealthy() == "listen_task_done"


def test_unhealthy_when_heartbeat_task_done():
    m = _make_healthy_manager()
    m._client._heartbeat_task = _done_task()
    assert m._channel_unhealthy() == "heartbeat_task_done"


def test_unhealthy_when_heartbeat_task_missing():
    m = _make_healthy_manager()
    m._client._heartbeat_task = None
    assert m._channel_unhealthy() == "heartbeat_task_done"


def test_healthy_when_all_signals_good():
    m = _make_healthy_manager()
    assert m._channel_unhealthy() is None


def test_unhealthy_degrades_gracefully_when_internals_renamed():
    """If a future realtime version renames _listen_task / _heartbeat_task,
    getattr returns None and the check still flags unhealthy rather than
    crashing the worker thread."""
    m = _make_manager()
    m._channel = MagicMock()
    m._channel.state = ChannelStates.JOINED
    # Bare object — no _listen_task / _heartbeat_task attributes at all.
    class _StubClient:
        is_connected = True
    m._client = _StubClient()
    # Should return a reason string, never raise.
    reason = m._channel_unhealthy()
    assert reason == "listen_task_done"


def test_run_loop_triggers_reconnect_on_dead_listen_task():
    """When the listen task is done, _run must call _full_reconnect on the
    next health-tick wake instead of waiting for the auth-refresh interval.
    """
    async def _scenario():
        m = _make_healthy_manager()
        m._async_stop = asyncio.Event()
        m._loop = asyncio.get_running_loop()

        # First _full_reconnect call (initial connect): succeeds, sets up the
        # healthy mock client. The loop then enters the steady-state while.
        # Second call (after we kill the listen task): records the call and
        # signals async_stop so the loop exits.
        reconnect_calls = []

        async def fake_reconnect():
            reconnect_calls.append(asyncio.get_running_loop().time())
            if len(reconnect_calls) == 1:
                # Initial connect — keep the healthy mock client/channel.
                return True
            # Reconnect after detecting the dead listen task — signal stop.
            m._async_stop.set()
            m._stop_event.set()
            return True

        m._full_reconnect = fake_reconnect  # type: ignore[method-assign]

        async def fake_refresh_auth():
            return None

        m._maybe_refresh_auth = fake_refresh_auth  # type: ignore[method-assign]

        # Kill the listen task immediately so the first health check trips.
        m._client._listen_task = _done_task()

        # Force a short health tick so the test runs quickly.
        import holmes.core.conversations_worker.realtime_manager as _rm
        original_tick = _rm.CONVERSATION_WORKER_REALTIME_HEALTH_TICK_SECONDS
        _rm.CONVERSATION_WORKER_REALTIME_HEALTH_TICK_SECONDS = 0.05
        try:
            await asyncio.wait_for(m._run(), timeout=2.0)
        finally:
            _rm.CONVERSATION_WORKER_REALTIME_HEALTH_TICK_SECONDS = original_tick

        # Must have reconnected at least twice (initial + recovery).
        assert len(reconnect_calls) >= 2

    asyncio.run(_scenario())


# ---- connectivity-warning log filter ----


def _make_record(name: str, level: int, msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=level, pathname="", lineno=0, msg=msg, args=(), exc_info=None
    )


@pytest.mark.parametrize(
    "msg",
    [
        "join push timeout for channel realtime:holmes:submit:acc:cluster",
        "WebSocket connection closed with code: 1006, reason: ",
        "Connection attempt failed: TimeoutError",
        "Connection failed permanently after 5 attempts. Error: ...",
    ],
)
def test_connectivity_filter_downgrades_known_errors_to_warning(msg):
    f = _RealtimeConnectivityWarningFilter()
    rec = _make_record("realtime._async.channel", logging.ERROR, msg)
    assert f.filter(rec) is True
    assert rec.levelno == logging.WARNING
    assert rec.levelname == "WARNING"


def test_connectivity_filter_leaves_unrelated_errors_alone():
    f = _RealtimeConnectivityWarningFilter()
    rec = _make_record("realtime._async.client", logging.ERROR, "Unrecognized message format")
    assert f.filter(rec) is True
    assert rec.levelno == logging.ERROR
    assert rec.levelname == "ERROR"


def test_connectivity_filter_leaves_non_error_levels_alone():
    f = _RealtimeConnectivityWarningFilter()
    rec = _make_record("realtime._async.channel", logging.INFO, "join push timeout for channel x")
    assert f.filter(rec) is True
    # Filter only acts on ERROR records.
    assert rec.levelno == logging.INFO


def test_install_realtime_log_filter_is_idempotent():
    # Clear any pre-existing instance so the count check is deterministic.
    for name in ("realtime._async.channel", "realtime._async.client"):
        lg = logging.getLogger(name)
        lg.filters = [f for f in lg.filters if not isinstance(f, _RealtimeConnectivityWarningFilter)]

    _install_realtime_log_filter_if_needed()
    _install_realtime_log_filter_if_needed()
    for name in ("realtime._async.channel", "realtime._async.client"):
        lg = logging.getLogger(name)
        installed = [f for f in lg.filters if isinstance(f, _RealtimeConnectivityWarningFilter)]
        assert len(installed) == 1


def test_install_realtime_log_filter_downgrades_live_log_records(caplog):
    _install_realtime_log_filter_if_needed()
    channel_logger = logging.getLogger("realtime._async.channel")
    client_logger = logging.getLogger("realtime._async.client")

    with caplog.at_level(logging.DEBUG, logger="realtime._async.channel"):
        channel_logger.error("join push timeout for channel realtime:holmes:submit:acc:cluster")
    with caplog.at_level(logging.DEBUG, logger="realtime._async.client"):
        client_logger.error("WebSocket connection closed with code: 1006, reason: ")

    join_record = next(r for r in caplog.records if "join push timeout" in r.getMessage())
    ws_record = next(r for r in caplog.records if "WebSocket connection closed" in r.getMessage())
    assert join_record.levelno == logging.WARNING
    assert ws_record.levelno == logging.WARNING


# ---- narrow exception handling in _full_reconnect ----


def test_full_reconnect_treats_transient_signin_error_as_warning(caplog):
    m = _make_manager()

    def boom_sign_in():
        raise ConnectionError("network unreachable")

    m.dal.sign_in = boom_sign_in
    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(m._full_reconnect())
    assert result is False
    warnings = [r for r in caplog.records if "will retry" in r.getMessage()]
    assert warnings and warnings[0].levelno == logging.WARNING


def test_full_reconnect_resurfaces_unexpected_signin_error(caplog):
    m = _make_manager()

    def boom_sign_in():
        raise ValueError("Authentication failed: no session returned")

    m.dal.sign_in = boom_sign_in
    with pytest.raises(ValueError):
        with caplog.at_level(logging.DEBUG):
            asyncio.run(m._full_reconnect())
    errors = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR and "not retrying" in r.getMessage()
    ]
    assert errors, "non-transient sign_in failure must be logged at ERROR"


def test_full_reconnect_treats_transient_subscribe_error_as_warning(caplog):
    m = _make_manager()
    m.dal.sign_in = MagicMock()

    async def boom_connect():
        raise TimeoutError("ws handshake timed out")

    m._connect_and_subscribe = boom_connect  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(m._full_reconnect())
    assert result is False
    warnings = [
        r
        for r in caplog.records
        if "Failed to reconnect" in r.getMessage() and r.levelno == logging.WARNING
    ]
    assert warnings


def test_full_reconnect_resurfaces_unexpected_subscribe_error(caplog):
    m = _make_manager()
    m.dal.sign_in = MagicMock()

    async def boom_connect():
        raise RuntimeError("library invariant broken")

    m._connect_and_subscribe = boom_connect  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        with caplog.at_level(logging.DEBUG):
            asyncio.run(m._full_reconnect())
    errors = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR
        and "Unexpected error during reconnect" in r.getMessage()
    ]
    assert errors


def test_transient_reconnect_exception_tuple_contents():
    # Lock down the expected transient set so future edits don't silently
    # widen the warning-only catch.
    assert SupabaseDnsException in _TRANSIENT_RECONNECT_EXCEPTIONS
    assert PGAPIError in _TRANSIENT_RECONNECT_EXCEPTIONS
    assert WebSocketException in _TRANSIENT_RECONNECT_EXCEPTIONS
    assert asyncio.TimeoutError in _TRANSIENT_RECONNECT_EXCEPTIONS
    assert TimeoutError in _TRANSIENT_RECONNECT_EXCEPTIONS
    assert ConnectionError in _TRANSIENT_RECONNECT_EXCEPTIONS
    assert OSError in _TRANSIENT_RECONNECT_EXCEPTIONS
