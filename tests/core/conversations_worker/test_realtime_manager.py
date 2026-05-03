"""Unit tests for RealtimeManager's testable (non-async) surface."""
import asyncio
import os
from unittest.mock import MagicMock

import pytest
import realtime._async.client as rt_client
from realtime._async.channel import ChannelStates

from holmes.core.conversations_worker.realtime_manager import (
    RealtimeManager,
    _install_proxy_patch_if_needed,
    broadcast_submit_topic,
    pg_changes_topic,
)


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


def test_install_proxy_patch_does_nothing_without_env(monkeypatch):
    """Patch installer must be a no-op when https_proxy is unset."""
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)

    # Reset patch state
    rt_client._holmes_proxy_patched = False
    original_connect = rt_client.connect
    _install_proxy_patch_if_needed()
    assert rt_client.connect is original_connect
    assert not getattr(rt_client, "_holmes_proxy_patched", False)


def test_install_proxy_patch_is_idempotent(monkeypatch):
    """Calling install twice should not double-patch.

    Monkeypatches ``_SocksProxy`` so the patching logic actually runs even
    when python-socks is not installed.
    """
    import holmes.core.conversations_worker.realtime_manager as _rm

    monkeypatch.setenv(
        "https_proxy", "http://user:pass@proxy.internal:8888"
    )
    # Ensure the patcher doesn't bail on _SocksProxy being None
    monkeypatch.setattr(_rm, "_SocksProxy", MagicMock())

    rt_client._holmes_proxy_patched = False
    original_connect = rt_client.connect
    _install_proxy_patch_if_needed()
    # After first call: connect must be replaced and flag set
    first_patched = rt_client.connect
    assert first_patched is not original_connect, "patch was not applied"
    assert getattr(rt_client, "_holmes_proxy_patched", False) is True

    _install_proxy_patch_if_needed()
    # After second call: connect must be unchanged (idempotent)
    second_patched = rt_client.connect
    assert first_patched is second_patched, "patch was reinstalled unexpectedly"
    assert getattr(rt_client, "_holmes_proxy_patched", False) is True

    # Cleanup: restore the original connect fn
    rt_client.connect = original_connect
    rt_client._holmes_proxy_patched = False


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
