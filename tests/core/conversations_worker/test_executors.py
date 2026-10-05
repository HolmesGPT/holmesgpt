"""ConversationExecutor: one pool's claim loop, dispatch, slots and logging."""

import logging
import threading
import time
from unittest.mock import MagicMock

import pytest

from holmes.core.conversations_worker.executors import (
    ConversationExecutor,
    _ActiveTask,
)
from holmes.core.conversations_worker.models import ConversationTask
from holmes.core.supabase_dal import ExecutorRpcUnsupportedError


def _dal():
    dal = MagicMock()
    dal.claim_n_pending_conversations = MagicMock(return_value=[])
    dal.update_conversation_status = MagicMock(return_value=True)
    return dal


def _executor(name="manual", size=5, dal=None, processor=None, running=True):
    """An executor whose pool is a MagicMock, so tests can drive
    claim_and_dispatch without real threads."""
    ex = ConversationExecutor(
        name,
        size,
        dal=dal or _dal(),
        holmes_id="h-test",
        processor=processor or MagicMock(),
    )
    if running:
        ex._running = True
        ex._pool = MagicMock()
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


def _slot(ex, started: float, conversation_id="c-slot", request_sequence=1):
    """Occupy one slot, as dispatch records it."""
    task = _task(conversation_id, request_sequence, ex.name)
    ex._active[task.active_key] = _ActiveTask(task, started)
    return task


# ---- claiming ----


def test_claims_only_free_slots_for_its_executor():
    """An executor claims only as many rows as it has free slots, filtered to
    its own name, and submits each straight to its pool — the claim RPC already
    landed the row in 'running'."""
    ex = _executor("manual", 5)
    ex.dal.claim_n_pending_conversations.return_value = [_row("c1"), _row("c2")]
    ex.claim_and_dispatch()
    ex.dal.claim_n_pending_conversations.assert_called_once_with(
        "h-test", 5, executor="manual"
    )
    assert ex._pool.submit.call_count == 2
    assert ("c1", 1) in ex._active and ("c2", 1) in ex._active
    ex.dal.update_conversation_status.assert_not_called()


def test_claim_passes_remaining_capacity_as_limit():
    ex = _executor("auto", 5)
    _slot(ex, 0.0, "existing1")
    _slot(ex, 0.0, "existing2")
    ex.claim_and_dispatch()
    ex.dal.claim_n_pending_conversations.assert_called_once_with(
        "h-test", 3, executor="auto"
    )


def test_claim_skipped_at_capacity():
    ex = _executor("manual", 1)
    _slot(ex, 0.0)
    ex.claim_and_dispatch()
    ex.dal.claim_n_pending_conversations.assert_not_called()
    ex._pool.submit.assert_not_called()


def test_claim_skipped_once_shut_down():
    """Shutting down: pending rows are left for another instance instead of
    being claimed only to be retired."""
    ex = _executor("manual", 5)
    ex._running = False
    ex.claim_and_dispatch()
    ex.dal.claim_n_pending_conversations.assert_not_called()


def test_saturated_auto_executor_does_not_block_manual_claims():
    """ROB-1369 acceptance: background work at full capacity must not delay a
    live user ask — 'manual' keeps claiming while 'auto' is saturated."""
    dal = _dal()
    auto = _executor("auto", 1, dal=dal)
    manual = _executor("manual", 2, dal=dal)
    _slot(auto, time.monotonic(), "triage-1")
    dal.claim_n_pending_conversations.side_effect = lambda _h, limit, executor=None: (
        [_row("chat-1", executor="manual")] if executor == "manual" else []
    )
    auto.claim_and_dispatch()
    manual.claim_and_dispatch()
    dal.claim_n_pending_conversations.assert_called_once_with(
        "h-test", 2, executor="manual"
    )
    manual._pool.submit.assert_called_once()
    auto._pool.submit.assert_not_called()


def test_new_size_takes_effect_in_claim_limit():
    ex = _executor("manual", 5)
    ex.set_max_concurrent(2)
    _slot(ex, 0.0, "busy")
    ex.claim_and_dispatch()
    ex.dal.claim_n_pending_conversations.assert_called_once_with(
        "h-test", 1, executor="manual"
    )


def test_backlog_drains_with_exact_claim_calls_and_limits():
    """Draining a 12-row backlog at capacity 5: one claim per iteration with
    limit == free slots, every row dispatched exactly once, ceil(12/5) == 3
    claims then a final empty one."""
    ex = _executor("manual", 5)
    pending = [f"c{i}" for i in range(12)]

    def fake_claim(_holmes_id, limit, executor=None):
        assert limit > 0
        assert executor == "manual"
        batch, pending[:] = pending[:limit], pending[limit:]
        return [_row(cid) for cid in batch]

    ex.dal.claim_n_pending_conversations.side_effect = fake_claim
    dispatched: list = []
    ex._pool.submit.side_effect = lambda _fn, task: dispatched.append(
        task.conversation_id
    )

    observed_limits, dispatched_per_iter, max_active = [], [], 0
    while pending or ex._active:
        free_before = 5 - len(ex._active)
        before = ex.dal.claim_n_pending_conversations.call_count
        dispatched_before = len(dispatched)
        ex.claim_and_dispatch()
        assert ex.dal.claim_n_pending_conversations.call_count == before + 1
        limit = ex.dal.claim_n_pending_conversations.call_args.args[1]
        assert limit == free_before
        observed_limits.append(limit)
        dispatched_per_iter.append(len(dispatched) - dispatched_before)
        max_active = max(max_active, len(ex._active))
        assert len(ex._active) <= 5
        ex._active.clear()

    assert observed_limits == [5, 5, 5]
    assert dispatched_per_iter == [5, 5, 2]
    assert max_active == 5
    assert sorted(dispatched) == sorted(f"c{i}" for i in range(12))

    calls_before = ex.dal.claim_n_pending_conversations.call_count
    ex.claim_and_dispatch()
    assert ex.dal.claim_n_pending_conversations.call_count == calls_before + 1
    assert len(dispatched) == 12


def test_two_instances_claim_disjoint_sets():
    """Cross-instance load balancing on one executor name: two Holmes pods
    draining the SAME 'manual' backlog never double-dispatch (the DB's FOR
    UPDATE SKIP LOCKED is simulated by an atomic slice per claim)."""
    pending = [f"c{i}" for i in range(12)]
    db_lock = threading.Lock()

    def fake_claim(_holmes_id, limit, executor=None):
        with db_lock:
            batch, pending[:] = pending[:limit], pending[limit:]
        return [_row(cid) for cid in batch]

    dispatched: dict = {}

    def make_instance(label):
        ex = _executor("manual", 5)
        ex.dal.claim_n_pending_conversations.side_effect = fake_claim

        def record(_fn, task, _label=label):
            assert task.conversation_id not in dispatched
            dispatched[task.conversation_id] = _label

        ex._pool.submit.side_effect = record
        return ex

    e1, e2 = make_instance("w1"), make_instance("w2")
    while pending or e1._active or e2._active:
        for ex in (e1, e2):
            ex.claim_and_dispatch()
            ex._active.clear()

    assert sorted(dispatched) == sorted(f"c{i}" for i in range(12))
    assert "w1" in dispatched.values() and "w2" in dispatched.values()


def test_signal_arriving_during_claim_is_not_lost():
    """The loop clears its event BEFORE claiming, so a broadcast that lands
    mid-claim re-sets it and the next wait() returns immediately."""
    ex = _executor("manual")

    def claim_then_broadcast(_holmes_id, _limit, executor=None):
        ex.wake()
        return []

    ex.dal.claim_n_pending_conversations.side_effect = claim_then_broadcast
    ex.notify_event.clear()
    ex.claim_and_dispatch()
    assert ex.notify_event.is_set()


def test_missing_executor_rpc_propagates():
    """Holmes is deployed after the migration; a PGRST202 is a deploy error
    that surfaces through the claim loop's exception logging, never a silent
    fallback to one shared pool."""
    ex = _executor("auto", 3)
    ex.dal.claim_n_pending_conversations.side_effect = ExecutorRpcUnsupportedError("x")
    with pytest.raises(ExecutorRpcUnsupportedError):
        ex.claim_and_dispatch()
    ex.dal.claim_n_pending_conversations.assert_called_once_with(
        "h-test", 3, executor="auto"
    )


def test_unparseable_row_is_failed_through_the_processor():
    ex = _executor("manual")
    bad = {"conversation_id": "c-bad", "request_sequence": 1}  # no account/cluster
    ex.dal.claim_n_pending_conversations.return_value = [bad, _row("c1")]
    ex.claim_and_dispatch()
    ex.processor.fail_row.assert_called_once_with(
        bad, "Failed to parse conversation row"
    )
    assert ("c1", 1) in ex._active and ("c-bad", 1) not in ex._active


# ---- dispatch ----


def test_dispatch_submits_without_status_transition():
    ex = _executor("manual")
    ex._dispatch(_task("c1"))
    ex.dal.update_conversation_status.assert_not_called()
    ex._pool.submit.assert_called_once()
    assert ("c1", 1) in ex._active


def test_dispatch_retires_claimed_row_once_shut_down():
    ex = _executor("manual")
    ex._running = False
    ex._dispatch(_task("c1"))
    ex._pool.submit.assert_not_called()
    assert ("c1", 1) not in ex._active
    ex.processor.retire.assert_called_once()
    assert ex.processor.retire.call_args.args[0].conversation_id == "c1"


def test_dispatch_retires_task_when_pool_shutdown_races():
    ex = _executor("manual")
    ex._pool.submit.side_effect = RuntimeError("cannot schedule new futures")
    ex._dispatch(_task("c1"))
    assert ("c1", 1) not in ex._active
    ex.processor.retire.assert_called_once()


def test_run_untracks_and_wakes_its_own_pool():
    """A finished conversation frees a slot on ITS executor only — that pool is
    woken to re-claim surplus 'pending' rows it left behind while saturated."""
    processor = MagicMock()
    ex = _executor("auto", processor=processor)
    other = _executor("manual", processor=processor)
    task = _task("c1", executor="auto")
    ex.track(task)
    ex.notify_event.clear()
    other.notify_event.clear()
    ex._run(task)
    processor.run.assert_called_once_with(task)
    assert ("c1", 1) not in ex._active
    assert ex.notify_event.is_set()
    assert not other.notify_event.is_set()


def test_run_frees_the_slot_even_when_the_processor_raises():
    processor = MagicMock()
    processor.run.side_effect = RuntimeError("escaped")
    ex = _executor("manual", processor=processor)
    task = _task("c1")
    ex.track(task)
    with pytest.raises(RuntimeError):
        ex._run(task)
    assert ("c1", 1) not in ex._active and ex.notify_event.is_set()


# ---- lifecycle ----


def test_loop_claims_when_woken_and_stops_cleanly():
    """End-to-end on a real thread: wake → claim; stop() ends the thread even
    while it waits with no timeout."""
    dal = _dal()
    claimed = threading.Event()
    dal.claim_n_pending_conversations.side_effect = lambda *a, **k: (claimed.set(), [])[
        1
    ]
    ex = ConversationExecutor(
        "manual", 2, dal=dal, holmes_id="h-test", processor=MagicMock()
    )
    ex.start()
    try:
        ex.wake()
        assert claimed.wait(timeout=3)
        dal.claim_n_pending_conversations.assert_called_with(
            "h-test", 2, executor="manual"
        )
    finally:
        ex.stop()
    assert ex._thread is None and ex._pool is None and not ex.running


def test_start_is_idempotent_and_pool_uses_the_thread_ceiling():
    ex = ConversationExecutor(
        "auto", 2, dal=_dal(), holmes_id="h", processor=MagicMock(), thread_ceiling=16
    )
    ex.start()
    try:
        pool = ex._pool
        ex.start()
        assert ex._pool is pool
        assert pool._max_workers == 16
        assert ex.max_concurrent == 2
    finally:
        ex.stop()


def test_creation_clamps_size_to_the_thread_ceiling(caplog):
    with caplog.at_level(logging.WARNING):
        ex = ConversationExecutor(
            "manual",
            5000,
            dal=_dal(),
            holmes_id="h",
            processor=MagicMock(),
            thread_ceiling=8,
        )
    assert ex.max_concurrent == 8
    assert any("capped at the thread ceiling" in r.getMessage() for r in caplog.records)


def test_set_max_concurrent_is_live_and_capped():
    ex = ConversationExecutor(
        "manual", 2, dal=_dal(), holmes_id="h", processor=MagicMock(), thread_ceiling=8
    )
    ex.notify_event.clear()
    assert ex.set_max_concurrent(5) is True
    assert ex.max_concurrent == 5 and ex.free_slots() == 5
    assert ex.notify_event.is_set()  # new slots → re-claim
    assert ex.set_max_concurrent(5) is False  # unchanged → no wake
    assert ex.set_max_concurrent(100) is True
    assert ex.max_concurrent == 8  # never above the pool's thread ceiling
    assert ex.set_max_concurrent(0) is True
    assert ex.max_concurrent == 1


def test_shutdown_closes_the_pool_without_waiting_and_is_idempotent():
    ex = _executor("manual")
    pool = ex._pool
    ex.shutdown()
    ex.shutdown()
    pool.shutdown.assert_called_once_with(wait=False)
    assert ex._pool is None and not ex.running and ex.notify_event.is_set()


def test_active_tasks_lists_in_flight_tasks():
    ex = _executor("manual", 3)
    t1 = _slot(ex, 0.0, "c1")
    t2 = _slot(ex, 0.0, "c2")
    assert {t.conversation_id for t in ex.active_tasks()} == {"c1", "c2"}
    ex.untrack(t1)
    assert ex.active_tasks() == [t2]
    assert ex.active_count() == 1 and ex.free_slots() == 2


# ---- saturation logging (ROB-759) ----


def test_saturation_logs_only_after_continuous_window(caplog):
    """Full capacity is a normal state under load, so the saturation INFO
    fires only after _SATURATION_LOG_AFTER_SECONDS of CONTINUOUS saturation
    and names the executor."""
    ex = _executor("manual", 1)
    _slot(ex, time.monotonic(), "conv-busy")

    def saturation_lines():
        return [
            r for r in caplog.records if "claim capacity saturated" in r.getMessage()
        ]

    with caplog.at_level(logging.INFO):
        ex.claim_and_dispatch()
        assert not saturation_lines()
        ex.claim_and_dispatch()
        assert not saturation_lines()

        ex._saturated_since = time.monotonic() - 61.0
        ex.claim_and_dispatch()
        assert len(saturation_lines()) == 1
        assert "conv-busy" in saturation_lines()[0].getMessage()
        assert "'manual'" in saturation_lines()[0].getMessage()

        ex.claim_and_dispatch()
        assert len(saturation_lines()) == 1

        ex._active.clear()
        ex.claim_and_dispatch()
        exits = [
            r for r in caplog.records if "capacity available again" in r.getMessage()
        ]
        assert len(exits) == 1
        assert ex._saturated_since is None and ex._saturation_logged is False


def test_brief_free_slot_resets_saturation_clock(caplog):
    ex = _executor("manual", 1)
    with caplog.at_level(logging.INFO):
        _slot(ex, time.monotonic(), "conv-a")
        ex.claim_and_dispatch()
        ex._saturated_since = time.monotonic() - 59.0
        ex._active.clear()
        ex.claim_and_dispatch()
        assert ex._saturated_since is None
        _slot(ex, time.monotonic(), "conv-b")
        ex.claim_and_dispatch()
    assert not [r for r in caplog.records if "claim capacity" in r.getMessage()]


def test_stuck_slot_emits_warning(monkeypatch, caplog):
    ex = _executor("manual", 1)
    monkeypatch.setattr(
        "holmes.core.conversations_worker.executors.CONVERSATION_WORKER_SLOT_STUCK_WARN_SECONDS",
        100.0,
    )
    _slot(ex, time.monotonic() - 150.0, "conv-stuck")
    ex._saturated_since = time.monotonic() - 10.0

    def stuck_warnings():
        return [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "slot(s) stuck" in r.getMessage()
        ]

    with caplog.at_level(logging.INFO):
        ex.claim_and_dispatch()
        assert len(stuck_warnings()) == 1
        assert "conv-stuck" in stuck_warnings()[0].getMessage()
        ex.claim_and_dispatch()
        assert len(stuck_warnings()) == 1
