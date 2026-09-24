"""Unit tests for worker lifecycle / executor claim loops / error handling."""

import threading
import logging
import time
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.conversations_worker.executors import (
    ConversationExecutor,
    ExecutorSettings,
)
from holmes.core.conversations_worker.models import (
    ConversationReassignedError,
    ConversationTask,
)
from holmes.core.conversations_worker.worker import (
    SHUTDOWN_ERROR_CODE,
    SHUTDOWN_REASON,
    ConversationWorker,
    _ActiveTask,
)
from holmes.core.supabase_dal import ExecutorRpcUnsupportedError


def _bare_worker(sizes=None, default_size=2, max_executors=16):
    w = ConversationWorker.__new__(ConversationWorker)
    w.dal = MagicMock()
    w.dal.enabled = True
    w.dal.update_conversation_status = MagicMock(return_value=True)
    w.dal.list_pending_conversation_executors = MagicMock(return_value=[])
    w.dal.claim_n_pending_conversations = MagicMock(return_value=[])
    w.config = MagicMock()
    w.chat_function = MagicMock()
    w.holmes_id = "h-test"
    w._running = True
    w._active_started = True
    w.dal.get_conversation_executor_sizes = MagicMock(return_value={})
    # `sizes` are the built-in per-name defaults, `default_size` the base size
    # for any other name; env is empty so tests are hermetic.
    w._executor_settings = ExecutorSettings(
        base_size=default_size,
        max_executors=max_executors,
        builtin_sizes=sizes if sizes is not None else {"manual": 5, "auto": 3},
        env={},
    )
    w._executors = {}
    w._executors_lock = threading.Lock()
    w._last_executor_reject_log = {}
    w._discovery_thread = None
    w._discovery_event = threading.Event()
    w._dispatch_lock = threading.Lock()
    w._realtime_manager = None
    w._tool_call_worker = MagicMock()
    w._realtime_verify_thread = None
    w._realtime_verify_stop = threading.Event()
    return w


def _fake_executor(w, name="manual", max_concurrent=None):
    """Register a running-looking executor whose pool submit is a MagicMock, so
    tests can drive _try_claim_and_dispatch without real threads."""
    ex = ConversationExecutor(
        name, max_concurrent or w._executor_settings.size_for(name)
    )
    ex._running = True
    ex._pool = MagicMock()
    w._executors[name] = ex
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
    """Occupy one executor slot, as _dispatch records it."""
    task = _task(conversation_id, request_sequence, ex.name)
    ex._active[task.active_key] = _ActiveTask(task, started)
    return task


def test_build_task_from_conversation_row_parses_required_fields():
    w = _bare_worker()
    row = {
        "conversation_id": "c1",
        "account_id": "a1",
        "cluster_id": "cl1",
        "origin": "chat",
        "request_sequence": 3,
        "metadata": {"foo": "bar"},
        "title": "hello",
        "user_id": "u-42",
        "executor": "auto",
    }
    task = w._build_task_from_conversation_row(row)
    assert task is not None
    assert task.conversation_id == "c1"
    assert task.request_sequence == 3
    assert task.metadata == {"foo": "bar"}
    assert task.title == "hello"
    assert task.user_id == "u-42"
    assert task.executor == "auto"


def test_build_task_from_conversation_row_tolerates_missing_fields():
    w = _bare_worker()
    row = {"conversation_id": "c1", "account_id": "a1", "cluster_id": "cl1"}
    task = w._build_task_from_conversation_row(row)
    assert task is not None
    assert task.request_sequence == 1
    assert task.origin == "chat"
    assert task.user_id is None
    # Rows written before the column existed run on the default executor.
    assert task.executor == "manual"


def test_build_task_claiming_executor_wins_over_row_column():
    """The pool that claimed the row owns it — a filtered claim can only return
    rows for that executor; the column is a fallback for a bare row."""
    w = _bare_worker()
    task = w._build_task_from_conversation_row(_row("c1", executor="auto"), "manual")
    assert task is not None and task.executor == "manual"


def test_build_task_from_conversation_row_returns_none_on_bad_input():
    w = _bare_worker()
    assert w._build_task_from_conversation_row({}) is None


# ---------------------------------------------------------------------------
# Claim + dispatch (per executor)
# ---------------------------------------------------------------------------


def test_try_claim_and_dispatch_claims_only_free_slots_for_its_executor():
    """An executor claims only as many rows as it has free slots, filtered to
    its own name, and submits each straight to its pool — the claim RPC already
    landed the row in 'running'."""
    w = _bare_worker(sizes={"manual": 5})
    ex = _fake_executor(w, "manual")
    w.dal.claim_n_pending_conversations.return_value = [_row("c1"), _row("c2")]
    w._try_claim_and_dispatch(ex)
    w.dal.claim_n_pending_conversations.assert_called_once_with(
        "h-test", 5, executor="manual"
    )
    assert ex._pool.submit.call_count == 2
    assert ("c1", 1) in ex._active and ("c2", 1) in ex._active
    w.dal.update_conversation_status.assert_not_called()


def test_try_claim_and_dispatch_passes_remaining_capacity_as_limit():
    w = _bare_worker(sizes={"auto": 5})
    ex = _fake_executor(w, "auto")
    _slot(ex, 0.0, "existing1")
    _slot(ex, 0.0, "existing2")
    w._try_claim_and_dispatch(ex)
    w.dal.claim_n_pending_conversations.assert_called_once_with(
        "h-test", 3, executor="auto"
    )


def test_try_claim_and_dispatch_skips_claim_when_at_capacity():
    w = _bare_worker(sizes={"manual": 1})
    ex = _fake_executor(w, "manual")
    _slot(ex, 0.0)
    w._try_claim_and_dispatch(ex)
    w.dal.claim_n_pending_conversations.assert_not_called()
    ex._pool.submit.assert_not_called()


def test_saturated_auto_executor_does_not_block_manual_claims():
    """ROB-1369 acceptance: background work at full capacity must not delay a
    live user ask — 'manual' keeps claiming while 'auto' is saturated."""
    w = _bare_worker(sizes={"manual": 2, "auto": 1})
    auto = _fake_executor(w, "auto")
    manual = _fake_executor(w, "manual")
    _slot(auto, time.monotonic(), "triage-1")

    def fake_claim(_holmes_id, limit, executor=None):
        assert executor == "manual", "only the manual pool should be claiming"
        return [_row("chat-1", executor="manual")]

    w.dal.claim_n_pending_conversations.side_effect = fake_claim

    w._try_claim_and_dispatch(auto)  # saturated → no RPC
    w._try_claim_and_dispatch(manual)  # claims its own row

    assert w.dal.claim_n_pending_conversations.call_count == 1
    assert ("chat-1", 1) in manual._active
    assert ("chat-1", 1) not in auto._active
    auto._pool.submit.assert_not_called()
    manual._pool.submit.assert_called_once()


def test_saturation_logs_only_after_continuous_window(caplog):
    """ROB-759: full capacity is a normal state under load, so the saturation
    INFO fires only after _SATURATION_LOG_AFTER_SECONDS of CONTINUOUS
    saturation and names the executor."""
    w = _bare_worker(sizes={"manual": 1})
    ex = _fake_executor(w, "manual")
    _slot(ex, time.monotonic(), "conv-busy")

    def saturation_lines():
        return [
            r for r in caplog.records if "claim capacity saturated" in r.getMessage()
        ]

    with caplog.at_level(logging.INFO):
        w._try_claim_and_dispatch(ex)
        assert not saturation_lines()
        w._try_claim_and_dispatch(ex)
        assert not saturation_lines()

        ex._saturated_since = time.monotonic() - 61.0
        w._try_claim_and_dispatch(ex)
        assert len(saturation_lines()) == 1
        assert "conv-busy" in saturation_lines()[0].getMessage()
        assert "'manual'" in saturation_lines()[0].getMessage()

        w._try_claim_and_dispatch(ex)
        assert len(saturation_lines()) == 1

        ex._active.clear()
        w._try_claim_and_dispatch(ex)
        exits = [
            r for r in caplog.records if "capacity available again" in r.getMessage()
        ]
        assert len(exits) == 1
        assert ex._saturated_since is None and ex._saturation_logged is False


def test_brief_free_slot_resets_saturation_clock(caplog):
    w = _bare_worker(sizes={"manual": 1})
    ex = _fake_executor(w, "manual")
    with caplog.at_level(logging.INFO):
        _slot(ex, time.monotonic(), "conv-a")
        w._try_claim_and_dispatch(ex)
        ex._saturated_since = time.monotonic() - 59.0
        ex._active.clear()
        w._try_claim_and_dispatch(ex)
        assert ex._saturated_since is None
        _slot(ex, time.monotonic(), "conv-b")
        w._try_claim_and_dispatch(ex)
    assert not [r for r in caplog.records if "claim capacity" in r.getMessage()]


def test_stuck_slot_emits_warning(monkeypatch, caplog):
    w = _bare_worker(sizes={"manual": 1})
    ex = _fake_executor(w, "manual")
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
        w._try_claim_and_dispatch(ex)
        assert len(stuck_warnings()) == 1
        assert "conv-stuck" in stuck_warnings()[0].getMessage()
        w._try_claim_and_dispatch(ex)
        assert len(stuck_warnings()) == 1


def test_backlog_drains_with_exact_claim_calls_and_limits():
    """Draining a 12-row backlog at capacity 5: one claim per iteration with
    limit == free slots, every row dispatched exactly once, ceil(12/5) == 3
    claims then a final empty one."""
    w = _bare_worker(sizes={"manual": 5})
    ex = _fake_executor(w, "manual")
    pending = [f"c{i}" for i in range(12)]

    def fake_claim(_holmes_id, limit, executor=None):
        assert limit > 0
        assert executor == "manual"
        batch, pending[:] = pending[:limit], pending[limit:]
        return [_row(cid) for cid in batch]

    w.dal.claim_n_pending_conversations.side_effect = fake_claim
    dispatched: list = []
    ex._pool.submit.side_effect = lambda _fn, task: dispatched.append(
        task.conversation_id
    )

    observed_limits, dispatched_per_iter, max_active = [], [], 0
    while pending or ex._active:
        free_before = 5 - len(ex._active)
        before = w.dal.claim_n_pending_conversations.call_count
        dispatched_before = len(dispatched)
        w._try_claim_and_dispatch(ex)
        assert w.dal.claim_n_pending_conversations.call_count == before + 1
        limit = w.dal.claim_n_pending_conversations.call_args.args[1]
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

    calls_before = w.dal.claim_n_pending_conversations.call_count
    w._try_claim_and_dispatch(ex)
    assert w.dal.claim_n_pending_conversations.call_count == calls_before + 1
    assert len(dispatched) == 12


def test_two_workers_claim_disjoint_sets():
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

    def make_worker(label):
        w = _bare_worker(sizes={"manual": 5})
        ex = _fake_executor(w, "manual")
        w.dal.claim_n_pending_conversations.side_effect = fake_claim

        def record(_fn, task, _label=label):
            assert task.conversation_id not in dispatched
            dispatched[task.conversation_id] = _label

        ex._pool.submit.side_effect = record
        return w, ex

    w1, e1 = make_worker("w1")
    w2, e2 = make_worker("w2")
    while pending or e1._active or e2._active:
        for w, ex in ((w1, e1), (w2, e2)):
            w._try_claim_and_dispatch(ex)
            ex._active.clear()

    assert sorted(dispatched) == sorted(f"c{i}" for i in range(12))
    assert "w1" in dispatched.values() and "w2" in dispatched.values()


def test_signal_arriving_during_claim_is_not_lost():
    """The executor loop clears its event BEFORE claiming, so a broadcast that
    lands mid-claim re-sets it and the next wait() returns immediately."""
    w = _bare_worker()
    ex = _fake_executor(w, "manual")

    def claim_then_broadcast(_holmes_id, _limit, executor=None):
        w.claim_pending_conversations("manual")
        return []

    w.dal.claim_n_pending_conversations.side_effect = claim_then_broadcast
    ex.notify_event.clear()
    w._try_claim_and_dispatch(ex)
    assert ex.notify_event.is_set()


def test_dispatch_submits_without_status_transition():
    w = _bare_worker()
    ex = _fake_executor(w, "manual")
    w._dispatch(_task("c1"))
    w.dal.update_conversation_status.assert_not_called()
    ex._pool.submit.assert_called_once()
    assert ("c1", 1) in ex._active


def test_dispatch_routes_by_task_executor():
    w = _bare_worker()
    manual = _fake_executor(w, "manual")
    auto = _fake_executor(w, "auto")
    w._dispatch(_task("c-auto", executor="auto"))
    auto._pool.submit.assert_called_once()
    manual._pool.submit.assert_not_called()
    assert ("c-auto", 1) in auto._active


def _assert_retired(w, cid="c1"):
    """A claimed row we could not run is closed out like a shutdown-interrupted
    turn: error event with the restart reason, then status 'timeout'."""
    events = w.dal.post_conversation_events.call_args.kwargs["events"]
    assert events[0]["data"]["reason"] == SHUTDOWN_REASON
    assert events[0]["data"]["error_code"] == SHUTDOWN_ERROR_CODE
    w.dal.update_conversation_status.assert_called_once_with(
        conversation_id=cid, request_sequence=1, assignee="h-test", status="timeout"
    )


def test_dispatch_retires_claimed_row_when_not_running():
    w = _bare_worker()
    ex = _fake_executor(w, "manual")
    w._running = False
    w._dispatch(_task("c1"))
    ex._pool.submit.assert_not_called()
    assert ("c1", 1) not in ex._active
    _assert_retired(w)


def test_dispatch_retires_task_when_executor_shutdown_races():
    w = _bare_worker()
    ex = _fake_executor(w, "manual")
    ex._pool.submit.side_effect = RuntimeError("cannot schedule new futures")
    w._dispatch(_task("c1"))
    assert ("c1", 1) not in ex._active
    _assert_retired(w)


def test_dispatch_retire_failure_is_swallowed():
    w = _bare_worker()
    w._running = False
    w.dal.post_conversation_events.side_effect = Exception("boom")
    w.dal.update_conversation_status.side_effect = Exception("boom")
    w._dispatch(_task("c1"))  # must not raise into the claim loop


def test_stop_does_not_deadlock_with_a_claim_loop_waiting_to_dispatch():
    """stop() holds _dispatch_lock while shutting pools down; the claim loop
    may be blocked on that very lock inside _dispatch. Joining it under the
    lock would deadlock — the join has to happen after the lock is released."""
    w = _bare_worker(sizes={"manual": 1})
    gate = threading.Event()
    reached = threading.Event()
    entered = threading.Event()

    def fake_claim(_holmes_id, limit, executor=None):
        entered.set()
        gate.wait(5)
        return [_row("c1")]

    w.dal.claim_n_pending_conversations.side_effect = fake_claim
    real_dispatch = w._dispatch

    def dispatch(task, executor=None):
        reached.set()
        real_dispatch(task, executor)

    w._dispatch = dispatch
    ex = w._get_or_create_executor("manual")
    assert ex is not None
    real_shutdown = ex.shutdown_pool

    def shutdown_pool():
        # Release the claim so it runs into _dispatch while we hold the lock.
        gate.set()
        assert reached.wait(5)
        time.sleep(0.05)
        real_shutdown()

    ex.shutdown_pool = shutdown_pool
    ex.wake()
    assert entered.wait(5)  # the claim RPC is in flight when stop() begins
    t0 = time.monotonic()
    w.stop()
    assert time.monotonic() - t0 < 4
    assert ex._thread is None
    _assert_retired(w)
    assert ("c1", 1) not in ex._active


def test_executor_creation_clamps_account_size_to_thread_ceiling():
    w = _bare_worker()
    w._executor_settings = ExecutorSettings(
        base_size=2, max_executors=16, thread_ceiling=8, env={}
    )
    w.dal.get_conversation_executor_sizes.return_value = {"manual": 5000}
    try:
        ex = w._get_or_create_executor("manual")
        assert ex is not None and ex.max_concurrent == 8
    finally:
        w.stop()


def test_executor_size_lookup_runs_outside_the_executors_lock():
    w = _bare_worker()

    def sizes():
        assert not w._executors_lock.locked()
        return {}

    w.dal.get_conversation_executor_sizes.side_effect = sizes
    try:
        assert w._get_or_create_executor("manual") is not None
    finally:
        w.stop()


def test_reject_log_rate_limit_is_per_message(caplog):
    w = _bare_worker(sizes={}, default_size=1, max_executors=1)
    try:
        with caplog.at_level(logging.WARNING):
            w._get_or_create_executor("a")
            w._get_or_create_executor("b")  # cap reached
            w._get_or_create_executor("b")  # rate-limited repeat
            w.claim_pending_conversations("UPPER")  # different cause: still logged
        msgs = [r.getMessage() for r in caplog.records]
        assert sum("executors already exist" in m for m in msgs) == 1
        assert sum("named invalid executor" in m for m in msgs) == 1
    finally:
        w.stop()


def test_process_conversation_safe_marks_failed_on_exception():
    w = _bare_worker()
    ex = _fake_executor(w, "manual")
    task = _task("c1")
    ex.track(task)

    def boom(*a, **kw):
        raise RuntimeError("synthetic failure")

    with patch.object(ConversationWorker, "_process_conversation", boom):
        w._process_conversation_safe(task)

    w.dal.post_conversation_events.assert_called_once()
    call_kwargs = w.dal.post_conversation_events.call_args[1]
    assert call_kwargs["conversation_id"] == "c1"
    desc = call_kwargs["events"][0]["data"]["description"]
    assert "synthetic failure" not in desc
    assert "internal error" in desc.lower()
    w.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1", request_sequence=1, assignee="h-test", status="failed"
    )
    assert ("c1", 1) not in ex._active


def test_process_conversation_safe_clears_active_and_wakes_its_executor():
    """A finished conversation frees a slot on ITS executor only — that pool is
    woken to re-claim surplus 'pending' rows it left behind while saturated."""
    w = _bare_worker()
    manual = _fake_executor(w, "manual")
    auto = _fake_executor(w, "auto")
    task = _task("c1", executor="auto")
    auto.track(task)
    auto.notify_event.clear()
    manual.notify_event.clear()
    with patch.object(
        ConversationWorker, "_process_conversation", lambda self, t: None
    ):
        w._process_conversation_safe(task)
    assert ("c1", 1) not in auto._active
    assert auto.notify_event.is_set()
    assert not manual.notify_event.is_set()


def test_process_conversation_safe_no_status_update_on_reassignment():
    w = _bare_worker()
    ex = _fake_executor(w, "manual")
    task = _task("c1")
    ex.track(task)

    def boom(*a, **kw):
        raise ConversationReassignedError("x")

    with patch.object(ConversationWorker, "_process_conversation", boom):
        w._process_conversation_safe(task)
    w.dal.update_conversation_status.assert_not_called()
    w.dal.post_conversation_events.assert_not_called()
    assert ("c1", 1) not in ex._active


# ---------------------------------------------------------------------------
# Executors: lazy creation, sizing, routing, caps
# ---------------------------------------------------------------------------


def test_no_executors_exist_before_a_request_names_one():
    w = _bare_worker()
    assert w.executor_names() == []


def test_named_broadcast_without_a_pool_routes_through_discovery():
    """A broadcast naming an executor with no pool yet does not create one —
    only DB-backed discovery does, for names that really have pending rows —
    so a stray publisher name cannot use up CONVERSATION_WORKER_MAX_EXECUTORS."""
    w = _bare_worker()
    w._discovery_event.clear()
    w.claim_pending_conversations("auto")
    assert w.executor_names() == []
    assert w._discovery_event.is_set()
    # Discovery finds rows for it and creates the pool …
    w.dal.list_pending_conversation_executors.return_value = ["auto"]
    try:
        w._discover_and_wake()
        assert w.executor_names() == ["auto"]
        # … and the next named broadcast wakes exactly that pool directly.
        ex = w._executors["auto"]
        ex.notify_event.clear()
        w._discovery_event.clear()
        w.claim_pending_conversations("auto")
        assert ex.notify_event.is_set()
        assert not w._discovery_event.is_set()
    finally:
        w.stop()


def test_unknown_executor_name_gets_default_size():
    w = _bare_worker(sizes={"manual": 5}, default_size=2)
    try:
        w._get_or_create_executor("nightly-report")
        assert w._executors["nightly-report"].max_concurrent == 2
    finally:
        w.stop()


def test_executor_creation_is_capped():
    w = _bare_worker(sizes={}, default_size=1, max_executors=2)
    try:
        assert w._get_or_create_executor("a") is not None
        assert w._get_or_create_executor("b") is not None
        assert w._get_or_create_executor("c") is None
        assert w.executor_names() == ["a", "b"]
    finally:
        w.stop()


@pytest.mark.parametrize("bad", ["", "Has Space", "UPPER", "x" * 65, "../etc", 42])
def test_invalid_executor_name_falls_back_to_discovery(bad):
    w = _bare_worker()
    w._discovery_event.clear()
    w.claim_pending_conversations(bad)
    assert w.executor_names() == []
    assert w._discovery_event.is_set()


def test_claim_pending_conversations_without_executor_wakes_discovery():
    w = _bare_worker()
    w._discovery_event.clear()
    w.claim_pending_conversations()
    assert w._discovery_event.is_set()
    assert w.executor_names() == []


def test_executors_are_not_created_before_active_workers_start():
    w = _bare_worker()
    w._active_started = False
    w.claim_pending_conversations("manual")
    assert w.executor_names() == []


def test_discover_and_wake_creates_executors_named_by_the_db():
    w = _bare_worker(sizes={"manual": 5, "auto": 3})
    w.dal.list_pending_conversation_executors.return_value = ["auto", "manual"]
    try:
        w._discover_and_wake()
        assert w.executor_names() == ["auto", "manual"]
    finally:
        w.stop()


def test_discover_and_wake_also_wakes_existing_idle_executors():
    w = _bare_worker()
    ex = _fake_executor(w, "manual")
    ex.notify_event.clear()
    w.dal.list_pending_conversation_executors.return_value = []
    w._discover_and_wake()
    assert ex.notify_event.is_set()


# ---------------------------------------------------------------------------
# Database without the executor-aware RPCs (migration 20260916073349 missing)
# ---------------------------------------------------------------------------


def test_missing_executor_rpc_is_a_plain_error_not_a_mode_switch():
    """Holmes is deployed after the migration; a PGRST202 is a deploy error.
    It surfaces through the claim/discovery loops' exception logging and the
    worker keeps its per-executor behavior (no single-pool fallback)."""
    w = _bare_worker()
    ex = _fake_executor(w, "auto")
    w.dal.claim_n_pending_conversations.side_effect = ExecutorRpcUnsupportedError("x")
    with pytest.raises(ExecutorRpcUnsupportedError):
        w._try_claim_and_dispatch(ex)
    w.dal.claim_n_pending_conversations.assert_called_once_with(
        "h-test", ex.max_concurrent, executor="auto"
    )
    w.dal.list_pending_conversation_executors.side_effect = ExecutorRpcUnsupportedError(
        "x"
    )
    with pytest.raises(ExecutorRpcUnsupportedError):
        w._discover_and_wake()
    assert w.executor_names() == ["auto"]


# ---------------------------------------------------------------------------
# Discovery loop wiring
# ---------------------------------------------------------------------------


def test_discovery_event_wakes_discovery_loop():
    w = _bare_worker()
    w._realtime_manager = MagicMock()
    w._realtime_manager.is_connected.return_value = True
    call_count = {"n": 0}

    def fake_discover():
        call_count["n"] += 1
        w._running = False

    w._discover_and_wake = fake_discover
    t = threading.Thread(target=w._discovery_loop)
    t.start()
    w._discovery_event.set()
    t.join(timeout=3)
    assert not t.is_alive()
    assert call_count["n"] == 1


def test_discovery_loop_initial_discovery_without_realtime():
    w = _bare_worker()
    w._realtime_manager = None
    call_count = {"n": 0}

    def fake_discover():
        call_count["n"] += 1
        w._running = False

    w._discover_and_wake = fake_discover
    t = threading.Thread(target=w._discovery_loop)
    t.start()
    t.join(timeout=3)
    assert not t.is_alive()
    assert call_count["n"] == 1


def test_executor_loop_claims_when_woken_and_stops_cleanly():
    """End-to-end on a real executor thread: wake → claim_fn runs; stop() ends
    the thread even while it waits with no timeout."""
    seen = []
    done = threading.Event()

    def claim_fn(ex):
        seen.append(ex.name)
        done.set()

    ex = ConversationExecutor("manual", 2)
    ex.start(claim_fn)
    try:
        ex.wake()
        assert done.wait(timeout=3)
        assert seen == ["manual"]
    finally:
        ex.stop()
    assert ex._thread is None and ex._pool is None


def _verify_worker():
    """Build a worker bare enough to drive _realtime_verify_loop directly,
    without spinning up the executor/claim-thread machinery."""
    w = _bare_worker()
    w._running = True
    w.config = MagicMock()
    return w


def test_realtime_verify_loop_updates_status_and_starts_workers_on_true():
    """A definitive True must flip HolmesStatus to env-var values, kick
    off the executor / claim loop / Realtime subscription, and exit the
    verifier loop."""
    w = _verify_worker()
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
    """A definitive False must call stop() WITHOUT having ever spun up
    the active workers; HolmesStatus is left at its default False so no
    extra status write is needed from this path."""
    w = _verify_worker()
    w.dal.is_realtime_enabled.return_value = False
    w.stop = MagicMock()  # don't actually tear down the bare worker
    w._start_active_workers = MagicMock()

    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        w._realtime_verify_loop()

    w.stop.assert_called_once_with()
    w._start_active_workers.assert_not_called()
    mock_update.assert_not_called()


def test_realtime_verify_loop_retries_on_connectivity_errors():
    """When is_realtime_enabled returns None (connectivity error), the
    loop must wait and retry until it gets a definitive answer."""
    w = _verify_worker()
    # Three connectivity failures, then True.
    w.dal.is_realtime_enabled.side_effect = [None, None, None, True]

    # Patch the stop event's wait to be non-blocking (no real backoff).
    original_wait = w._realtime_verify_stop.wait

    def fast_wait(timeout=None):
        return False  # never signalled, return immediately

    w._realtime_verify_stop.wait = fast_wait  # type: ignore[assignment]

    try:
        with patch(
            "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
        ) as mock_update:
            w._realtime_verify_loop()
    finally:
        w._realtime_verify_stop.wait = original_wait  # type: ignore[assignment]

    assert w.dal.is_realtime_enabled.call_count == 4
    mock_update.assert_called_once_with(w.dal, w.config, realtime_available=True)


def test_realtime_verify_loop_exits_when_stop_event_set():
    """If stop() has already been called when the verifier starts, the
    loop must bail out before issuing any probe."""
    w = _verify_worker()
    w.dal.is_realtime_enabled.return_value = None  # always inconclusive
    w._realtime_verify_stop.set()

    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        w._realtime_verify_loop()

    w.dal.is_realtime_enabled.assert_not_called()
    mock_update.assert_not_called()


def test_realtime_verify_loop_exits_when_running_flag_cleared():
    """If _running is cleared (worker stopped), the loop must not start a
    new probe iteration."""
    w = _verify_worker()
    w._running = False
    w.dal.is_realtime_enabled.return_value = None

    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        w._realtime_verify_loop()

    w.dal.is_realtime_enabled.assert_not_called()
    mock_update.assert_not_called()


def test_realtime_verify_loop_surfaces_unexpected_exceptions():
    """An unexpected exception from is_realtime_enabled (i.e. one that the
    DAL itself didn't convert to None) is a programming defect — the loop
    must surface it rather than silently retry forever. The DAL already
    folds all transport-level errors into a None return, so anything that
    escapes here is something we can't sensibly back off from."""
    w = _verify_worker()
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
    import logging

    w = _verify_worker()
    # First call raises a transient error; second call returns True so the
    # loop terminates.
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
    """A non-transient exception (e.g. AttributeError from a bug) must be
    logged at ERROR and propagate out of the loop instead of being silently
    retried."""
    import logging

    w = _verify_worker()
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
# Startup / verifier integration
# ---------------------------------------------------------------------------


def test_start_only_spawns_verifier_not_active_workers():
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct"
    dal.cluster = "cl"
    block_event = threading.Event()

    def blocking_check():
        block_event.wait(timeout=5)
        return None

    dal.is_realtime_enabled.side_effect = blocking_check
    w = ConversationWorker(dal=dal, config=MagicMock(), chat_function=MagicMock())
    try:
        w.start()
        assert w._realtime_verify_thread is not None
        assert w._realtime_verify_thread.is_alive()
        assert w._discovery_thread is None
        assert w._realtime_manager is None
        assert w.executor_names() == []
    finally:
        block_event.set()
        w.stop()


def test_start_starts_discovery_but_no_executors_after_definitive_true():
    """Once realtime is verified the discovery loop runs — but no executor pool
    exists until a request names one (ROB-1369)."""
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct"
    dal.cluster = "cl"
    dal.is_realtime_enabled.return_value = True
    dal.list_pending_conversation_executors.return_value = []
    w = ConversationWorker(dal=dal, config=MagicMock(), chat_function=MagicMock())
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
            assert w._discovery_thread is not None
            assert w.executor_names() == []
            # A pending row naming an executor creates it, sized from settings.
            w.dal.list_pending_conversation_executors.return_value = ["auto"]
            w._discover_and_wake()
            assert w.executor_names() == ["auto"]
        finally:
            w.stop()
    assert w.executor_names() == []


def test_start_does_not_start_active_workers_after_definitive_false():
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct"
    dal.cluster = "cl"
    dal.is_realtime_enabled.return_value = False
    w = ConversationWorker(dal=dal, config=MagicMock(), chat_function=MagicMock())
    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        w.start()
        w._realtime_verify_thread.join(timeout=3)
        assert not w._realtime_verify_thread.is_alive()
    mock_update.assert_not_called()
    assert w._discovery_thread is None
    assert w._realtime_manager is None
    assert w.executor_names() == []
    assert w._running is False


def test_start_skips_when_dal_disabled():
    dal = MagicMock()
    dal.enabled = False
    w = ConversationWorker(dal=dal, config=MagicMock(), chat_function=MagicMock())
    w.start()
    assert w._running is False
    assert w._realtime_verify_thread is None
    dal.is_realtime_enabled.assert_not_called()


# ---------------------------------------------------------------------------
# Shutdown: retire in-flight conversations ("Holmes Restarted")
# ---------------------------------------------------------------------------


def _active(w, conversation_id="c1", request_sequence=1, executor="manual"):
    ex = w._executors.get(executor) or _fake_executor(w, executor)
    task = _task(conversation_id, request_sequence, executor)
    ex.track(task)
    return task


def test_timeout_active_conversations_posts_reason_then_sets_timeout():
    w = _bare_worker()
    _active(w, "c1", 2)
    w._timeout_active_conversations()
    w.dal.post_conversation_events.assert_called_once()
    kwargs = w.dal.post_conversation_events.call_args.kwargs
    assert kwargs["conversation_id"] == "c1"
    assert kwargs["request_sequence"] == 2
    (event,) = kwargs["events"]
    assert event["event"] == "error"
    assert event["data"]["reason"] == SHUTDOWN_REASON
    assert SHUTDOWN_REASON in event["data"]["description"]
    assert event["data"]["error_code"] == SHUTDOWN_ERROR_CODE
    w.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1", request_sequence=2, assignee="h-test", status="timeout"
    )


def test_timeout_active_conversations_handles_every_in_flight_row_across_executors():
    w = _bare_worker()
    _active(w, "c1", 1, "manual")
    _active(w, "c2", 1, "auto")
    _active(w, "c1", 2, "manual")
    w._timeout_active_conversations()
    assert w.dal.update_conversation_status.call_count == 3
    handled = {
        (c.kwargs["conversation_id"], c.kwargs["request_sequence"])
        for c in w.dal.update_conversation_status.call_args_list
    }
    assert handled == {("c1", 1), ("c1", 2), ("c2", 1)}


def test_timeout_active_conversations_noop_when_idle():
    w = _bare_worker()
    _fake_executor(w, "manual")
    w._timeout_active_conversations()
    w.dal.post_conversation_events.assert_not_called()
    w.dal.update_conversation_status.assert_not_called()


def test_timeout_conversation_writes_timeout_only():
    w = _bare_worker()
    task = _active(w, "c1", 1)
    w.dal.update_conversation_status = MagicMock(return_value=False)
    w._timeout_conversation(task)
    statuses = [
        c.kwargs["status"] for c in w.dal.update_conversation_status.call_args_list
    ]
    assert statuses == ["timeout"]


def test_timeout_conversation_stops_when_row_was_reassigned():
    w = _bare_worker()
    task = _active(w, "c1", 1)
    w.dal.update_conversation_status = MagicMock(
        side_effect=ConversationReassignedError("MISMATCH")
    )
    w._timeout_conversation(task)
    assert w.dal.update_conversation_status.call_count == 1


def test_timeout_active_conversations_continues_after_one_row_fails():
    w = _bare_worker()
    _active(w, "c1", 1)
    _active(w, "c2", 1)
    w.dal.post_conversation_events = MagicMock(
        side_effect=[RuntimeError("supabase down"), 7]
    )
    w._timeout_active_conversations()
    assert w.dal.update_conversation_status.call_count == 2


def test_stop_retires_in_flight_conversations_and_stops_executors():
    w = _bare_worker()
    _active(w, "c1", 1, "manual")
    _active(w, "c2", 1, "auto")
    pools = [w._executors["manual"]._pool, w._executors["auto"]._pool]
    w.stop()
    statuses = {
        c.kwargs["conversation_id"]: c.kwargs["status"]
        for c in w.dal.update_conversation_status.call_args_list
    }
    assert statuses == {"c1": "timeout", "c2": "timeout"}
    assert w._running is False
    assert w.executor_names() == []
    for pool in pools:
        pool.shutdown.assert_called_once_with(wait=False)


def test_stop_survives_a_failing_retirement():
    w = _bare_worker()
    _active(w, "c1", 1)
    w._timeout_active_conversations = MagicMock(side_effect=RuntimeError("boom"))
    w.stop()
    assert w._running is False
    w._tool_call_worker.stop.assert_called_once()


def test_timeout_active_conversations_stops_at_the_budget(caplog):
    from itertools import chain, repeat

    w = _bare_worker()
    _active(w, "c1", 1)
    _active(w, "c2", 1)
    _active(w, "c3", 1)
    clock = chain([0.0, 0.0], repeat(1_000.0))
    with patch(
        "holmes.core.conversations_worker.worker.time.monotonic",
        lambda: next(clock),
    ):
        with caplog.at_level(logging.WARNING, logger="root"):
            w._timeout_active_conversations()
    assert w.dal.update_conversation_status.call_count == 1
    assert any(
        "budget" in r.getMessage() and "2 conversation(s)" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


# ---------------------------------------------------------------------------
# Pool sizes from account settings (Settings → LLM Models / AI Triage)
# ---------------------------------------------------------------------------


def test_executor_created_with_account_setting_size():
    w = _bare_worker(sizes={"manual": 10, "auto": 2})
    w.dal.get_conversation_executor_sizes.return_value = {"manual": 4}
    try:
        w._get_or_create_executor("manual")
        w._get_or_create_executor("auto")
        assert w._executors["manual"].max_concurrent == 4  # account setting wins
        assert w._executors["auto"].max_concurrent == 2  # built-in default
    finally:
        w.stop()


def test_discovery_applies_changed_account_sizes_live():
    """Changing the concurrency in the UI must not need a Holmes restart: the
    next discovery tick resizes running pools."""
    w = _bare_worker(sizes={"manual": 10, "auto": 2})
    manual = _fake_executor(w, "manual", max_concurrent=10)
    auto = _fake_executor(w, "auto", max_concurrent=2)
    manual.notify_event.clear()
    w.dal.get_conversation_executor_sizes.return_value = {"manual": 3, "auto": 6}
    w.dal.list_pending_conversation_executors.return_value = []
    w._discover_and_wake()
    assert manual.max_concurrent == 3
    assert auto.max_concurrent == 6
    # A resize wakes the pool so newly freed/added slots are claimed.
    assert manual.notify_event.is_set()
    # Removing the setting falls back to env/built-in.
    w.dal.get_conversation_executor_sizes.return_value = {}
    w._discover_and_wake()
    assert manual.max_concurrent == 10 and auto.max_concurrent == 2


def test_account_sizes_read_failure_falls_back_to_defaults():
    w = _bare_worker(sizes={"manual": 10})
    w.dal.get_conversation_executor_sizes.side_effect = RuntimeError("db down")
    try:
        w._get_or_create_executor("manual")
        assert w._executors["manual"].max_concurrent == 10
    finally:
        w.stop()


def test_new_size_takes_effect_in_claim_limit():
    w = _bare_worker(sizes={"manual": 5})
    ex = _fake_executor(w, "manual", max_concurrent=5)
    ex.set_max_concurrent(2)
    _slot(ex, 0.0, "busy")
    w._try_claim_and_dispatch(ex)
    w.dal.claim_n_pending_conversations.assert_called_once_with(
        "h-test", 1, executor="manual"
    )
