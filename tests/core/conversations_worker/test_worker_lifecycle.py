"""ConversationRuntime: Realtime verification, start/stop, the shutdown sweep."""

import logging
import threading
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.conversations_worker.executors import ConversationExecutor
from holmes.core.conversations_worker.models import ConversationTask
from holmes.core.conversations_worker.processor import (
    SHUTDOWN_ERROR_CODE,
    SHUTDOWN_REASON,
    ConversationProcessor,
)
from holmes.core.conversations_worker.registry import ExecutorRegistry
from holmes.core.conversations_worker.sizing import ExecutorSizing
from holmes.core.conversations_worker.worker import ConversationRuntime


def _dal():
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct"
    dal.cluster = "cl"
    dal.update_conversation_status = MagicMock(return_value=True)
    dal.list_pending_conversation_executors = MagicMock(return_value=[])
    dal.claim_n_pending_conversations = MagicMock(return_value=[])
    dal.get_conversation_executor_sizes = MagicMock(return_value={})
    return dal


def _bare_runtime():
    """A runtime past verification, with a started registry and no threads."""
    w = ConversationRuntime.__new__(ConversationRuntime)
    w.dal = _dal()
    w.config = MagicMock()
    w.holmes_id = "h-test"
    w.processor = ConversationProcessor(dal=w.dal, config=w.config, holmes_id="h-test")
    w.executors = ExecutorRegistry(
        dal=w.dal,
        holmes_id="h-test",
        processor=w.processor,
        sizing=ExecutorSizing(base_size=2, builtin_sizes={"manual": 5, "auto": 3}),
    )
    w.executors._started = True
    w._tool_call_worker = MagicMock()
    w._realtime_manager = None
    w._running = True
    w._active_started = True
    w._realtime_verify_thread = None
    w._realtime_verify_stop = threading.Event()
    return w


def _task(cid="c1", seq=1, executor="manual"):
    return ConversationTask(
        conversation_id=cid,
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=seq,
        executor=executor,
    )


def _active(w, conversation_id="c1", request_sequence=1, executor="manual"):
    """Occupy a slot on `executor`, creating a fake running pool if needed."""
    ex = w.executors.get(executor)
    if ex is None:
        ex = ConversationExecutor(
            executor, 5, dal=w.dal, holmes_id="h-test", processor=w.processor
        )
        ex._running = True
        ex._pool = MagicMock()
        w.executors._executors[executor] = ex
    task = _task(conversation_id, request_sequence, executor)
    ex.track(task)
    return task


# ---------------------------------------------------------------------------
# Shutdown: retire in-flight conversations ("Holmes Restarted")
# ---------------------------------------------------------------------------


def test_retire_in_flight_posts_reason_then_sets_timeout():
    w = _bare_runtime()
    _active(w, "c1", 2)
    w._retire_in_flight()
    w.dal.post_conversation_events.assert_called_once()
    kwargs = w.dal.post_conversation_events.call_args.kwargs
    assert kwargs["conversation_id"] == "c1"
    assert kwargs["request_sequence"] == 2
    (event,) = kwargs["events"]
    assert event["event"] == "error"
    assert event["data"]["reason"] == SHUTDOWN_REASON
    assert event["data"]["error_code"] == SHUTDOWN_ERROR_CODE
    w.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1", request_sequence=2, assignee="h-test", status="timeout"
    )


def test_retire_in_flight_handles_every_row_across_executors():
    w = _bare_runtime()
    _active(w, "c1", 1, "manual")
    _active(w, "c2", 1, "auto")
    _active(w, "c1", 2, "manual")
    w._retire_in_flight()
    handled = {
        (c.kwargs["conversation_id"], c.kwargs["request_sequence"])
        for c in w.dal.update_conversation_status.call_args_list
    }
    assert handled == {("c1", 1), ("c1", 2), ("c2", 1)}


def test_retire_in_flight_noop_when_idle():
    w = _bare_runtime()
    w._retire_in_flight()
    w.dal.post_conversation_events.assert_not_called()
    w.dal.update_conversation_status.assert_not_called()


def test_retire_in_flight_continues_after_one_row_fails():
    w = _bare_runtime()
    _active(w, "c1", 1)
    _active(w, "c2", 1)
    w.dal.post_conversation_events = MagicMock(
        side_effect=[RuntimeError("supabase down"), 7]
    )
    w._retire_in_flight()
    assert w.dal.update_conversation_status.call_count == 2


def test_retire_in_flight_stops_at_the_budget(caplog):
    from itertools import chain, repeat

    w = _bare_runtime()
    _active(w, "c1", 1)
    _active(w, "c2", 1)
    _active(w, "c3", 1)
    clock = chain([0.0, 0.0], repeat(1_000.0))
    with patch(
        "holmes.core.conversations_worker.worker.time.monotonic",
        lambda: next(clock),
    ):
        with caplog.at_level(logging.WARNING, logger="root"):
            w._retire_in_flight()
    assert w.dal.update_conversation_status.call_count == 1
    assert any(
        "budget" in r.getMessage() and "2 conversation(s)" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


def test_stop_retires_in_flight_conversations_and_stops_executors():
    w = _bare_runtime()
    _active(w, "c1", 1, "manual")
    _active(w, "c2", 1, "auto")
    pools = [w.executors.get("manual")._pool, w.executors.get("auto")._pool]
    w.stop()
    statuses = {
        c.kwargs["conversation_id"]: c.kwargs["status"]
        for c in w.dal.update_conversation_status.call_args_list
    }
    assert statuses == {"c1": "timeout", "c2": "timeout"}
    assert w._running is False
    assert w.executors.names() == []
    for pool in pools:
        pool.shutdown.assert_called_once_with(wait=False)
    w._tool_call_worker.stop.assert_called_once()


def test_stop_survives_a_failing_retirement():
    w = _bare_runtime()
    _active(w, "c1", 1)
    w._retire_in_flight = MagicMock(side_effect=RuntimeError("boom"))
    w.stop()
    assert w._running is False
    w._tool_call_worker.stop.assert_called_once()
    assert w.executors.names() == []


# ---------------------------------------------------------------------------
# Realtime verifier
# ---------------------------------------------------------------------------


def test_realtime_verify_loop_updates_status_and_starts_workers_on_true():
    """A definitive True must flip HolmesStatus to env-var values, start the
    consumers, and exit the verifier loop."""
    w = _bare_runtime()
    w.dal.is_realtime_enabled.return_value = True
    w._start_active_workers = MagicMock()

    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        w._realtime_verify_loop()

    mock_update.assert_called_once_with(w.dal, w.config, realtime_available=True)
    w._start_active_workers.assert_called_once_with()
    assert w._running is True
    w.dal.is_realtime_enabled.assert_called_once_with()


def test_realtime_verify_loop_shuts_down_on_definitive_false():
    """A definitive False must call stop() WITHOUT having ever started the
    consumers; HolmesStatus is left at its default False so no extra status
    write is needed from this path."""
    w = _bare_runtime()
    w.dal.is_realtime_enabled.return_value = False
    w.stop = MagicMock()
    w._start_active_workers = MagicMock()

    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        w._realtime_verify_loop()

    w.stop.assert_called_once_with()
    w._start_active_workers.assert_not_called()
    mock_update.assert_not_called()


def test_realtime_verify_loop_retries_on_connectivity_errors():
    """When is_realtime_enabled returns None (connectivity error), the loop
    must wait and retry until it gets a definitive answer."""
    w = _bare_runtime()
    w.dal.is_realtime_enabled.side_effect = [None, None, None, True]
    w._start_active_workers = MagicMock()
    w._realtime_verify_stop.wait = lambda timeout=None: False  # type: ignore[assignment]

    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        w._realtime_verify_loop()

    assert w.dal.is_realtime_enabled.call_count == 4
    mock_update.assert_called_once_with(w.dal, w.config, realtime_available=True)


def test_realtime_verify_loop_exits_when_stop_event_set():
    w = _bare_runtime()
    w.dal.is_realtime_enabled.return_value = None
    w._realtime_verify_stop.set()
    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        w._realtime_verify_loop()
    w.dal.is_realtime_enabled.assert_not_called()
    mock_update.assert_not_called()


def test_realtime_verify_loop_exits_when_running_flag_cleared():
    w = _bare_runtime()
    w._running = False
    w.dal.is_realtime_enabled.return_value = None
    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        w._realtime_verify_loop()
    w.dal.is_realtime_enabled.assert_not_called()
    mock_update.assert_not_called()


def test_realtime_verify_loop_surfaces_unexpected_exceptions():
    """An exception the DAL did not convert to None is a programming defect —
    the loop must surface it rather than silently retry forever."""
    w = _bare_runtime()
    w.dal.is_realtime_enabled.side_effect = RuntimeError("boom")
    w._realtime_verify_stop.wait = lambda timeout=None: False  # type: ignore[assignment]
    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        with pytest.raises(RuntimeError):
            w._realtime_verify_loop()
    assert w.dal.is_realtime_enabled.call_count == 1
    mock_update.assert_not_called()


def test_realtime_verify_loop_warns_on_transient_connectivity_exception(caplog):
    """A transient connectivity exception (e.g. ConnectionError) must be
    treated as a None/retry and logged at WARNING, not ERROR."""
    w = _bare_runtime()
    w.dal.is_realtime_enabled.side_effect = [ConnectionError("dns blip"), True]
    w._start_active_workers = MagicMock()
    w._realtime_verify_stop.wait = lambda timeout=None: False  # type: ignore[assignment]

    with caplog.at_level(logging.DEBUG, logger="root"):
        with patch(
            "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
        ):
            w._realtime_verify_loop()

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "Connectivity error" in r.getMessage()
    ]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert warnings
    assert not errors, "transient connectivity must not log at ERROR"
    assert w.dal.is_realtime_enabled.call_count == 2


def test_realtime_verify_loop_surfaces_non_transient_exception(caplog):
    w = _bare_runtime()
    w.dal.is_realtime_enabled.side_effect = AttributeError("dal misconfigured")
    with pytest.raises(AttributeError):
        with caplog.at_level(logging.DEBUG, logger="root"):
            w._realtime_verify_loop()
    errors = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR and "not retrying" in r.getMessage()
    ]
    assert errors, "non-transient defect must be logged at ERROR"


# ---------------------------------------------------------------------------
# Startup / verifier integration (real constructor)
# ---------------------------------------------------------------------------


def test_start_only_spawns_verifier_not_consumers():
    dal = _dal()
    block_event = threading.Event()

    def blocking_check():
        block_event.wait(timeout=5)
        return None

    dal.is_realtime_enabled.side_effect = blocking_check
    w = ConversationRuntime(dal=dal, config=MagicMock())
    try:
        w.start()
        assert w._realtime_verify_thread is not None
        assert w._realtime_verify_thread.is_alive()
        assert w.executors._discovery_thread is None
        assert w._realtime_manager is None
        assert w.executors.names() == []
    finally:
        block_event.set()
        w.stop()


def test_start_runs_discovery_but_creates_no_executors_after_definitive_true():
    """Once realtime is verified the discovery loop runs — but no executor pool
    exists until a request names one (ROB-1369)."""
    dal = _dal()
    dal.is_realtime_enabled.return_value = True
    w = ConversationRuntime(dal=dal, config=MagicMock())
    with patch(
        "holmes.core.conversations_worker.worker.CONVERSATION_WORKER_REALTIME_ENABLED",
        False,
    ), patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        try:
            w.start()
            w._realtime_verify_thread.join(timeout=3)
            assert not w._realtime_verify_thread.is_alive()
            mock_update.assert_called_once_with(dal, w.config, realtime_available=True)
            assert w.executors._discovery_thread is not None
            assert w.executors.names() == []
            # A pending row naming an executor creates it, sized from settings.
            dal.list_pending_conversation_executors.return_value = ["auto"]
            w.executors.discover()
            assert w.executors.names() == ["auto"]
        finally:
            w.stop()
    assert w.executors.names() == []


def test_start_does_not_start_consumers_after_definitive_false():
    dal = _dal()
    dal.is_realtime_enabled.return_value = False
    w = ConversationRuntime(dal=dal, config=MagicMock())
    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        w.start()
        w._realtime_verify_thread.join(timeout=3)
        assert not w._realtime_verify_thread.is_alive()
    mock_update.assert_not_called()
    assert w.executors._discovery_thread is None
    assert w._realtime_manager is None
    assert w.executors.names() == []
    assert w._running is False


def test_start_skips_when_dal_disabled():
    dal = _dal()
    dal.enabled = False
    w = ConversationRuntime(dal=dal, config=MagicMock())
    w.start()
    assert w._running is False
    assert w._realtime_verify_thread is None
    dal.is_realtime_enabled.assert_not_called()


def test_realtime_manager_receives_the_registry_as_routing_target():
    w = _bare_runtime()
    w._active_started = False
    with patch(
        "holmes.core.conversations_worker.worker.CONVERSATION_WORKER_REALTIME_ENABLED",
        True,
    ), patch("holmes.core.conversations_worker.worker.RealtimeWorker") as rw:
        try:
            w._start_active_workers()
        finally:
            w.stop()
    assert rw.call_args.kwargs["on_new_pending"] == w.executors.on_pending
