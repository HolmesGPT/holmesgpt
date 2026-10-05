"""ExecutorRegistry: pools on demand, the cap, live sizing, discovery."""

import logging
import threading
import time
from unittest.mock import MagicMock

import pytest

from holmes.core.conversations_worker.executors import ConversationExecutor
from holmes.core.conversations_worker.models import ConversationTask
from holmes.core.conversations_worker.processor import (
    EXECUTOR_UNAVAILABLE_ERROR_CODE,
    ConversationProcessor,
)
from holmes.core.conversations_worker.registry import ExecutorRegistry
from holmes.core.conversations_worker.sizing import ExecutorSizing
from holmes.core.supabase_dal import ExecutorRpcUnsupportedError


def _dal():
    dal = MagicMock()
    dal.enabled = True
    dal.update_conversation_status = MagicMock(return_value=True)
    dal.list_pending_conversation_executors = MagicMock(return_value=[])
    dal.claim_n_pending_conversations = MagicMock(return_value=[])
    dal.get_conversation_executor_sizes = MagicMock(return_value={})
    return dal


def _registry(sizes=None, default_size=2, max_executors=16, ceiling=64, started=True):
    """A started registry with a real processor over a mocked DAL. `sizes` are
    the built-in per-name defaults, `default_size` the base for other names."""
    dal = _dal()
    processor = ConversationProcessor(dal=dal, config=MagicMock(), holmes_id="h-test")
    reg = ExecutorRegistry(
        dal=dal,
        holmes_id="h-test",
        processor=processor,
        sizing=ExecutorSizing(
            base_size=default_size,
            builtin_sizes=sizes if sizes is not None else {"manual": 5, "auto": 3},
            thread_ceiling=ceiling,
        ),
        max_executors=max_executors,
    )
    reg._started = started
    return reg


def _fake_executor(reg, name="manual", max_concurrent=None):
    """Register a running-looking pool whose submit is a MagicMock."""
    ex = ConversationExecutor(
        name,
        max_concurrent or reg.sizing.size_for(name),
        dal=reg.dal,
        holmes_id=reg.holmes_id,
        processor=reg.processor,
    )
    ex._running = True
    ex._pool = MagicMock()
    reg._executors[name] = ex
    return ex


def _row(cid, executor="manual", seq=1):
    return {
        "conversation_id": cid,
        "account_id": "a1",
        "cluster_id": "cl1",
        "origin": "chat",
        "request_sequence": seq,
        "metadata": {},
        "executor": executor,
    }


def _task(cid="c1", seq=1, executor="manual"):
    return ConversationTask(
        conversation_id=cid,
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=seq,
        executor=executor,
    )


# ---- creation on demand ----


def test_no_executors_exist_before_a_request_names_one():
    assert _registry().names() == []


def test_named_broadcast_without_a_pool_routes_through_discovery():
    """A broadcast naming an executor with no pool yet does not create one —
    only DB-backed discovery does, for names that really have pending rows —
    so a stray publisher name cannot use up the executor cap."""
    reg = _registry()
    reg._discovery_event.clear()
    reg.on_pending("auto")
    assert reg.names() == []
    assert reg._discovery_event.is_set()
    # Discovery finds rows for it and creates the pool …
    reg.dal.list_pending_conversation_executors.return_value = ["auto"]
    try:
        reg.discover()
        assert reg.names() == ["auto"]
        # … and the next named broadcast wakes exactly that pool directly.
        ex = reg.get("auto")
        ex.notify_event.clear()
        reg._discovery_event.clear()
        reg.on_pending("auto")
        assert ex.notify_event.is_set()
        assert not reg._discovery_event.is_set()
    finally:
        reg.stop()


def test_unknown_executor_name_gets_default_size():
    reg = _registry(sizes={"manual": 5}, default_size=2)
    try:
        assert reg.get_or_create("nightly-report").max_concurrent == 2
    finally:
        reg.stop()


def test_executor_creation_is_capped():
    reg = _registry(sizes={}, default_size=1, max_executors=2)
    try:
        assert reg.get_or_create("a") is not None
        assert reg.get_or_create("b") is not None
        assert reg.get_or_create("c") is None
        assert reg.names() == ["a", "b"]
    finally:
        reg.stop()


def test_rows_of_an_executor_past_the_cap_are_failed_not_left_pending():
    """Every instance applies the same cap, so a row naming a third executor
    would hang 'pending' with no error. Discovery claims and fails it with a
    message that says why."""
    reg = _registry(sizes={}, default_size=1, max_executors=2)
    reg.dal.list_pending_conversation_executors.return_value = ["a", "b", "c"]

    def fake_claim(_holmes_id, limit, executor=None):
        return [_row("c1", executor="c")] if executor == "c" else []

    reg.dal.claim_n_pending_conversations.side_effect = fake_claim
    try:
        reg.discover()
        assert reg.names() == ["a", "b"]
        events = reg.dal.post_conversation_events.call_args.kwargs["events"]
        assert events[0]["data"]["error_code"] == EXECUTOR_UNAVAILABLE_ERROR_CODE
        assert "limit 2" in events[0]["data"]["description"]
        reg.dal.update_conversation_status.assert_called_once_with(
            conversation_id="c1", request_sequence=1, assignee="h-test", status="failed"
        )
    finally:
        reg.stop()


def test_pools_go_to_the_first_names_in_db_order_when_capped():
    reg = _registry(sizes={}, default_size=1, max_executors=2)
    reg.dal.list_pending_conversation_executors.return_value = ["c", "a", "b"]
    try:
        reg.discover()
        assert reg.names() == ["a", "c"]
    finally:
        reg.stop()


def test_discovery_does_not_fail_rows_before_start():
    reg = _registry(sizes={}, default_size=1, max_executors=1, started=False)
    reg.dal.list_pending_conversation_executors.return_value = ["a"]
    reg.discover()
    assert reg.names() == []
    reg.dal.claim_n_pending_conversations.assert_not_called()


@pytest.mark.parametrize("bad", ["", "Has Space", "UPPER", "x" * 65, "../etc", 42])
def test_invalid_executor_name_falls_back_to_discovery(bad):
    reg = _registry()
    reg._discovery_event.clear()
    reg.on_pending(bad)
    assert reg.names() == []
    assert reg._discovery_event.is_set()


def test_on_pending_without_executor_wakes_discovery():
    reg = _registry()
    reg._discovery_event.clear()
    reg.on_pending()
    assert reg._discovery_event.is_set()
    assert reg.names() == []


def test_executors_are_not_created_before_start():
    reg = _registry(started=False)
    assert reg.get_or_create("manual") is None
    assert reg.names() == []


def test_discover_creates_executors_named_by_the_db():
    reg = _registry(sizes={"manual": 5, "auto": 3})
    reg.dal.list_pending_conversation_executors.return_value = ["auto", "manual"]
    try:
        reg.discover()
        assert reg.names() == ["auto", "manual"]
        assert reg.get("manual").max_concurrent == 5
        assert reg.get("auto").max_concurrent == 3
    finally:
        reg.stop()


def test_discover_also_wakes_existing_idle_executors():
    reg = _registry()
    ex = _fake_executor(reg, "manual")
    ex.notify_event.clear()
    reg.discover()
    assert ex.notify_event.is_set()


def test_missing_discovery_rpc_propagates():
    reg = _registry()
    _fake_executor(reg, "auto")
    reg.dal.list_pending_conversation_executors.side_effect = (
        ExecutorRpcUnsupportedError("x")
    )
    with pytest.raises(ExecutorRpcUnsupportedError):
        reg.discover()
    assert reg.names() == ["auto"]


def test_active_tasks_aggregates_across_executors():
    reg = _registry()
    _fake_executor(reg, "manual").track(_task("c1", 1, "manual"))
    _fake_executor(reg, "auto").track(_task("c2", 1, "auto"))
    assert {t.conversation_id for t in reg.active_tasks()} == {"c1", "c2"}


# ---- sizes from account settings (Settings → LLMs / Triage) ----


def test_executor_created_with_account_setting_size():
    reg = _registry(sizes={"manual": 10, "auto": 2})
    reg.dal.get_conversation_executor_sizes.return_value = {"manual": 4}
    try:
        assert reg.get_or_create("manual").max_concurrent == 4  # account setting wins
        assert reg.get_or_create("auto").max_concurrent == 2  # built-in default
    finally:
        reg.stop()


def test_discovery_applies_changed_account_sizes_live():
    """Changing the concurrency in the UI must not need a Holmes restart: the
    next discovery tick resizes running pools."""
    reg = _registry(sizes={"manual": 10, "auto": 2})
    manual = _fake_executor(reg, "manual", max_concurrent=10)
    auto = _fake_executor(reg, "auto", max_concurrent=2)
    manual.notify_event.clear()
    reg.dal.get_conversation_executor_sizes.return_value = {"manual": 3, "auto": 6}
    reg.discover()
    assert manual.max_concurrent == 3
    assert auto.max_concurrent == 6
    # A resize wakes the pool so newly freed/added slots are claimed.
    assert manual.notify_event.is_set()
    # Removing the setting falls back to the built-in default.
    reg.dal.get_conversation_executor_sizes.return_value = {}
    reg.discover()
    assert manual.max_concurrent == 10 and auto.max_concurrent == 2


def test_account_sizes_read_failure_falls_back_to_defaults():
    reg = _registry(sizes={"manual": 10})
    reg.dal.get_conversation_executor_sizes.side_effect = RuntimeError("db down")
    try:
        assert reg.get_or_create("manual").max_concurrent == 10
    finally:
        reg.stop()


def test_executor_creation_clamps_account_size_to_thread_ceiling():
    reg = _registry(ceiling=8)
    reg.dal.get_conversation_executor_sizes.return_value = {"manual": 5000}
    try:
        assert reg.get_or_create("manual").max_concurrent == 8
    finally:
        reg.stop()


def test_size_lookup_runs_outside_the_registry_lock():
    reg = _registry()

    def sizes():
        assert not reg._lock.locked()
        return {}

    reg.dal.get_conversation_executor_sizes.side_effect = sizes
    try:
        assert reg.get_or_create("manual") is not None
    finally:
        reg.stop()


def test_discovery_reads_account_sizes_once_per_tick():
    reg = _registry()
    reg.dal.list_pending_conversation_executors.return_value = ["auto", "manual"]
    try:
        reg.discover()
        assert reg.dal.get_conversation_executor_sizes.call_count == 1
    finally:
        reg.stop()


def test_reject_log_rate_limit_is_per_message(caplog):
    reg = _registry(sizes={}, default_size=1, max_executors=1)
    try:
        with caplog.at_level(logging.WARNING):
            reg.get_or_create("a")
            reg.get_or_create("b")  # cap reached
            reg.get_or_create("b")  # rate-limited repeat
            reg.on_pending("UPPER")  # different cause: still logged
        msgs = [r.getMessage() for r in caplog.records]
        assert sum("executors already exist" in m for m in msgs) == 1
        assert sum("named invalid executor" in m for m in msgs) == 1
    finally:
        reg.stop()


# ---- stop ----


def test_stop_closes_every_pool():
    reg = _registry()
    pools = [_fake_executor(reg, "manual")._pool, _fake_executor(reg, "auto")._pool]
    reg.stop()
    assert reg.names() == []
    assert reg._started is False
    for pool in pools:
        pool.shutdown.assert_called_once_with(wait=False)


def test_stop_does_not_deadlock_with_a_claim_loop_in_dispatch():
    """A claim loop may be inside its own dispatch while the registry stops.
    Shutting the pool is non-blocking and the join is bounded, so stop()
    returns promptly and the claimed row is retired, not lost."""
    reg = _registry(sizes={"manual": 1})
    gate = threading.Event()
    entered = threading.Event()

    def fake_claim(_holmes_id, limit, executor=None):
        entered.set()
        gate.wait(5)
        return [_row("c1")]

    reg.dal.claim_n_pending_conversations.side_effect = fake_claim
    ex = reg.get_or_create("manual")
    assert ex is not None
    real_shutdown = ex.shutdown

    def shutdown():
        # Release the claim so the loop runs into dispatch while we shut down.
        gate.set()
        time.sleep(0.05)
        real_shutdown()

    ex.shutdown = shutdown
    ex.wake()
    assert entered.wait(5)  # the claim RPC is in flight when stop() begins
    t0 = time.monotonic()
    reg.stop()
    assert time.monotonic() - t0 < 4
    assert ex._thread is None
    # The claimed row is never dropped: it was submitted before the pool
    # closed (still in flight for the runtime's sweep, or already processed)
    # or dispatch retired it.
    written = {
        c.kwargs["conversation_id"]: c.kwargs["status"]
        for c in reg.dal.update_conversation_status.call_args_list
    }
    assert ("c1", 1) in ex._active or written.get("c1") in ("timeout", "failed")


# ---- discovery loop wiring ----


def test_discovery_event_wakes_discovery_loop():
    reg = _registry()
    calls = {"n": 0}

    def fake_discover():
        calls["n"] += 1
        reg._started = False

    reg.discover = fake_discover
    t = threading.Thread(target=reg._discovery_loop, args=(lambda: True, False))
    t.start()
    reg._discovery_event.set()
    t.join(timeout=3)
    assert not t.is_alive()
    assert calls["n"] == 1


def test_discovery_loop_discovers_immediately_without_realtime():
    reg = _registry()
    calls = {"n": 0}

    def fake_discover():
        calls["n"] += 1
        reg._started = False

    reg.discover = fake_discover
    t = threading.Thread(target=reg._discovery_loop, args=(lambda: False, True))
    t.start()
    t.join(timeout=3)
    assert not t.is_alive()
    assert calls["n"] == 1


def test_start_runs_the_discovery_loop_and_stop_ends_it():
    reg = _registry(started=False)
    discovered = threading.Event()
    reg.dal.list_pending_conversation_executors.side_effect = lambda: (
        discovered.set(),
        [],
    )[1]
    reg.start(realtime_connected=lambda: False, discover_immediately=True)
    try:
        assert discovered.wait(3)
        assert reg._discovery_thread is not None and reg._discovery_thread.is_alive()
    finally:
        reg.stop()
    assert reg._discovery_thread is None
