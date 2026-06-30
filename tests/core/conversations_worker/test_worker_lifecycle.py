"""Unit tests for worker lifecycle / claim-loop / error handling."""
import threading
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.conversations_worker.models import (
    ConversationReassignedError,
    ConversationTask,
)
from holmes.core.conversations_worker.worker import ConversationWorker


def _bare_worker():
    w = ConversationWorker.__new__(ConversationWorker)
    w.dal = MagicMock()
    w.dal.enabled = True
    w.dal.update_conversation_status = MagicMock(return_value=True)
    w.config = MagicMock()
    w.chat_function = MagicMock()
    w.holmes_id = "h-test"
    w._running = True
    w._claim_thread = None
    w._notify_event = threading.Event()
    w._executor = MagicMock()
    w._active_conversation_ids = set()
    w._active_lock = threading.Lock()
    w._dispatch_lock = threading.Lock()
    w._realtime_manager = None
    w._realtime_verify_thread = None
    w._realtime_verify_stop = threading.Event()
    return w


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
    }
    task = w._build_task_from_conversation_row(row)
    assert task is not None
    assert task.conversation_id == "c1"
    assert task.request_sequence == 3
    assert task.metadata == {"foo": "bar"}
    assert task.title == "hello"
    # user_id from the Conversations row is surfaced on the task so the
    # ChatRequest construction can fall back to it when the per-event
    # data doesn't carry user_id.
    assert task.user_id == "u-42"


def test_build_task_from_conversation_row_tolerates_missing_fields():
    w = _bare_worker()
    row = {"conversation_id": "c1", "account_id": "a1", "cluster_id": "cl1"}
    task = w._build_task_from_conversation_row(row)
    assert task is not None
    assert task.request_sequence == 1
    assert task.origin == "chat"
    # user_id is optional on the Conversations row (e.g. older rows that
    # predate the column); the task should still build cleanly.
    assert task.user_id is None


def test_build_task_from_conversation_row_returns_none_on_bad_input():
    w = _bare_worker()
    task = w._build_task_from_conversation_row({})  # missing required fields
    assert task is None


def test_try_claim_and_dispatch_claims_only_free_slots(monkeypatch):
    """The worker claims only as many conversations as it has free pool slots
    (MAX_CONCURRENT - active) and submits each straight to the executor — the
    claim RPC already landed the row in 'running', so there is no separate
    queued→running transition."""
    w = _bare_worker()
    # Pin the capacity explicitly (like the neighboring tests) rather than
    # relying on the default, so this stays valid if the default changes.
    capacity = 5
    monkeypatch.setattr(
        "holmes.core.conversations_worker.worker.CONVERSATION_WORKER_MAX_CONCURRENT",
        capacity,
    )
    w.dal.claim_n_pending_conversations.return_value = [
        {
            "conversation_id": "c1",
            "account_id": "a1",
            "cluster_id": "cl1",
            "origin": "chat",
            "request_sequence": 1,
            "metadata": {},
        },
        {
            "conversation_id": "c2",
            "account_id": "a1",
            "cluster_id": "cl1",
            "origin": "chat",
            "request_sequence": 1,
            "metadata": {},
        },
    ]
    w._try_claim_and_dispatch()
    # With no active work, the worker asks for the full configured capacity.
    w.dal.claim_n_pending_conversations.assert_called_once_with("h-test", capacity)
    # Both claimed conversations should have been submitted to the executor.
    assert w._executor.submit.call_count == 2
    assert ("c1", 1) in w._active_conversation_ids
    assert ("c2", 1) in w._active_conversation_ids
    # No status transition happens on dispatch — the claim already set 'running'.
    w.dal.update_conversation_status.assert_not_called()


def test_try_claim_and_dispatch_passes_remaining_capacity_as_limit(monkeypatch):
    """The claim limit reflects the slots NOT already taken by running work."""
    w = _bare_worker()
    monkeypatch.setattr(
        "holmes.core.conversations_worker.worker.CONVERSATION_WORKER_MAX_CONCURRENT",
        5,
    )
    # Two conversations already running -> only 3 free slots remain
    # (free = MAX_CONCURRENT - active; there is no longer a local queue).
    w._active_conversation_ids = {"existing1", "existing2"}
    w.dal.claim_n_pending_conversations.return_value = []
    w._try_claim_and_dispatch()
    w.dal.claim_n_pending_conversations.assert_called_once_with("h-test", 3)


def test_try_claim_and_dispatch_skips_claim_when_at_capacity(monkeypatch):
    """When at capacity the worker must NOT claim — surplus stays pending in
    the DB (claimable by another Holmes instance) instead of being hoarded."""
    w = _bare_worker()
    monkeypatch.setattr(
        "holmes.core.conversations_worker.worker.CONVERSATION_WORKER_MAX_CONCURRENT",
        1,
    )
    # Already have one active conversation -> zero free slots.
    w._active_conversation_ids = {"existing"}
    w._try_claim_and_dispatch()
    # No claim RPC is issued at all when there is no free capacity.
    w.dal.claim_n_pending_conversations.assert_not_called()
    w._executor.submit.assert_not_called()


def test_backlog_drains_with_exact_claim_calls_and_limits(monkeypatch):
    """Exact-accounting test: draining a 12-row backlog at capacity 5 must

      * call claim exactly once per iteration that has free capacity,
      * pass limit == free slots on every call (never more),
      * dispatch exactly the rows it claimed — each conversation once, never
        exceeding MAX_CONCURRENT — so no work is missed or double-claimed,
      * issue ceil(12/5) == 3 claim calls total, then a final empty claim.
    """
    w = _bare_worker()
    monkeypatch.setattr(
        "holmes.core.conversations_worker.worker.CONVERSATION_WORKER_MAX_CONCURRENT",
        5,
    )

    pending = [f"c{i}" for i in range(12)]

    def fake_claim(_holmes_id, limit):
        assert limit > 0  # the worker must never call claim with no free capacity
        batch, pending[:] = pending[:limit], pending[limit:]
        return [
            {
                "conversation_id": cid,
                "account_id": "a",
                "cluster_id": "cl",
                "origin": "chat",
                "request_sequence": 1,
                "metadata": {},
            }
            for cid in batch
        ]

    w.dal.claim_n_pending_conversations.side_effect = fake_claim

    dispatched: list = []
    w._executor.submit.side_effect = lambda _fn, task: dispatched.append(
        task.conversation_id
    )

    observed_limits = []
    dispatched_per_iter = []
    max_active = 0
    # Each iteration: claim+dispatch, then simulate every running conv finishing
    # (frees the whole pool for the next claim), until the backlog is drained.
    while pending or w._active_conversation_ids:
        free_before = 5 - len(w._active_conversation_ids)
        before = w.dal.claim_n_pending_conversations.call_count
        dispatched_before = len(dispatched)
        w._try_claim_and_dispatch()
        after = w.dal.claim_n_pending_conversations.call_count

        assert after == before + 1, "exactly one claim call per iteration"
        limit = w.dal.claim_n_pending_conversations.call_args.args[1]
        assert limit == free_before, "claim limit must equal free capacity"
        observed_limits.append(limit)
        dispatched_per_iter.append(len(dispatched) - dispatched_before)

        max_active = max(max_active, len(w._active_conversation_ids))
        assert len(w._active_conversation_ids) <= 5, "never exceed MAX_CONCURRENT"

        for key in list(w._active_conversation_ids):
            w._active_conversation_ids.discard(key)

    # The whole pool is freed each iteration, so every claim requests the full
    # 5 free slots; the final batch simply returns fewer rows (the remaining 2).
    assert observed_limits == [5, 5, 5]
    assert dispatched_per_iter == [5, 5, 2]  # ceil(12/5): 5, 5, then 2
    assert max_active == 5
    # Every conversation dispatched exactly once — none missed, none duplicated.
    assert sorted(dispatched) == sorted(f"c{i}" for i in range(12))

    # Backlog drained: one more pass issues a claim that returns nothing and
    # dispatches nothing (no phantom work).
    calls_before = w.dal.claim_n_pending_conversations.call_count
    w._try_claim_and_dispatch()
    assert w.dal.claim_n_pending_conversations.call_count == calls_before + 1
    assert len(dispatched) == 12


def test_two_workers_claim_disjoint_sets(monkeypatch):
    """Cross-instance load balancing: two workers draining the SAME backlog
    must never both dispatch the same conversation. The DB guarantees this
    with FOR UPDATE SKIP LOCKED; here we simulate that guarantee (each claim
    atomically removes its own slice under a lock) and assert the worker side
    never double-dispatches, every row is handled exactly once, and both
    workers actually participate."""
    monkeypatch.setattr(
        "holmes.core.conversations_worker.worker.CONVERSATION_WORKER_MAX_CONCURRENT",
        5,
    )
    pending = [f"c{i}" for i in range(12)]
    db_lock = threading.Lock()

    def fake_claim(_holmes_id, limit):
        # SKIP LOCKED: each caller atomically takes a disjoint slice.
        with db_lock:
            batch, pending[:] = pending[:limit], pending[limit:]
        return [
            {
                "conversation_id": cid,
                "account_id": "a",
                "cluster_id": "cl",
                "origin": "chat",
                "request_sequence": 1,
                "metadata": {},
            }
            for cid in batch
        ]

    dispatched: dict = {}  # conversation_id -> worker label (detects double dispatch)

    def make_worker(label):
        w = _bare_worker()
        w.dal.claim_n_pending_conversations.side_effect = fake_claim

        def record(_fn, task, _label=label):
            assert task.conversation_id not in dispatched, (
                f"{task.conversation_id} dispatched twice (by "
                f"{dispatched.get(task.conversation_id)} and {_label})"
            )
            dispatched[task.conversation_id] = _label

        w._executor.submit.side_effect = record
        return w

    w1 = make_worker("w1")
    w2 = make_worker("w2")

    # Drain round-robin; each worker frees its whole pool after each claim.
    while pending or w1._active_conversation_ids or w2._active_conversation_ids:
        for w in (w1, w2):
            w._try_claim_and_dispatch()
            for key in list(w._active_conversation_ids):
                w._active_conversation_ids.discard(key)

    assert sorted(dispatched) == sorted(f"c{i}" for i in range(12))
    assert "w1" in dispatched.values() and "w2" in dispatched.values(), (
        "backlog exceeded one pool, so both workers should have claimed some"
    )


def test_signal_arriving_during_claim_is_not_lost(monkeypatch):
    """The claim loop clears _notify_event BEFORE claiming (see _claim_loop), so
    a broadcast that lands mid-claim re-sets the event and the next wait()
    returns immediately — the wakeup is never lost. This reproduces that exact
    ordering and asserts the event survives a claim that signals itself."""
    w = _bare_worker()
    monkeypatch.setattr(
        "holmes.core.conversations_worker.worker.CONVERSATION_WORKER_MAX_CONCURRENT",
        5,
    )

    def claim_then_broadcast(_holmes_id, _limit):
        # A 'pending_conversations' broadcast lands while we're mid-claim.
        w.claim_pending_conversations()  # == _notify_event.set()
        return []

    w.dal.claim_n_pending_conversations.side_effect = claim_then_broadcast

    # One loop-body iteration in the same order as _claim_loop:
    w._notify_event.clear()          # loop clears before claiming
    w._try_claim_and_dispatch()      # claim runs; a broadcast arrives mid-claim
    # Event is set again -> the loop's next wait() wakes immediately to re-claim.
    assert w._notify_event.is_set()
    assert w._notify_event.wait(timeout=0) is True


def test_dispatch_submits_without_status_transition():
    """_dispatch submits a claimed conversation straight to the executor and
    tracks it as active. The claim RPC already set the row to 'running', so no
    update_conversation_status call happens here."""
    w = _bare_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    w._dispatch(task)
    w.dal.update_conversation_status.assert_not_called()
    w._executor.submit.assert_called_once()
    assert ("c1", 1) in w._active_conversation_ids


def test_dispatch_noop_when_not_running():
    """If the worker is stopping, _dispatch must not submit or track the task."""
    w = _bare_worker()
    w._running = False
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    w._dispatch(task)
    w._executor.submit.assert_not_called()
    assert ("c1", 1) not in w._active_conversation_ids


def test_dispatch_drops_task_when_executor_shutdown_races():
    """If the executor is torn down between the running check and submit, the
    in-flight tracking must be rolled back so capacity isn't leaked."""
    w = _bare_worker()
    w._executor.submit.side_effect = RuntimeError("cannot schedule new futures")
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    w._dispatch(task)
    assert ("c1", 1) not in w._active_conversation_ids


def test_process_conversation_safe_marks_failed_on_exception():
    w = _bare_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )

    def boom(*a, **kw):
        raise RuntimeError("synthetic failure")

    with patch.object(ConversationWorker, "_process_conversation", boom):
        w._process_conversation_safe(task)

    # Error event should be posted before marking as failed
    w.dal.post_conversation_events.assert_called_once()
    call_kwargs = w.dal.post_conversation_events.call_args[1]
    assert call_kwargs["conversation_id"] == "c1"
    error_events = call_kwargs["events"]
    assert error_events[0]["event"] == "error"
    # The error event must use a generic message, not the raw exception text
    desc = error_events[0]["data"]["description"]
    assert "synthetic failure" not in desc, "Raw exception text must not leak into error events"
    assert "internal error" in desc.lower()

    w.dal.update_conversation_status.assert_called_once_with(
        conversation_id="c1",
        request_sequence=1,
        assignee="h-test",
        status="failed",
    )
    # active conversation cleared
    assert ("c1", 1) not in w._active_conversation_ids


def test_process_conversation_safe_clears_active_on_success():
    w = _bare_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    with patch.object(ConversationWorker, "_process_conversation", lambda self, t: None):
        w._process_conversation_safe(task)

    assert ("c1", 1) not in w._active_conversation_ids


def test_process_conversation_safe_wakes_claim_loop_to_reclaim():
    """When a conversation finishes a pool slot frees up — the worker must
    signal the claim loop so it re-claims any surplus 'pending' rows it left
    behind while at capacity."""
    w = _bare_worker()
    w._notify_event.clear()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )
    with patch.object(ConversationWorker, "_process_conversation", lambda self, t: None):
        w._process_conversation_safe(task)

    assert w._notify_event.is_set()


def test_process_conversation_safe_no_status_update_on_reassignment():
    """On ConversationReassignedError the worker must NOT call
    update_conversation_status — the conversation's state is already
    being handled by whoever reassigned it."""
    w = _bare_worker()
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )

    def boom(*a, **kw):
        raise ConversationReassignedError("x")

    with patch.object(ConversationWorker, "_process_conversation", boom):
        w._process_conversation_safe(task)

    w.dal.update_conversation_status.assert_not_called()
    w.dal.post_conversation_events.assert_not_called()
    assert ("c1", 1) not in w._active_conversation_ids


def test_notify_event_wakes_claim_loop():
    """The claim loop should wake quickly when notify_event is set.

    When _realtime_manager is set, the initial claim is deferred until
    the SUBSCRIBED callback fires on_new_pending (which sets _notify_event).
    This test simulates that by setting the event externally.
    """
    w = _bare_worker()
    w._realtime_manager = MagicMock()
    w._realtime_manager.is_connected.return_value = True

    call_count = {"n": 0}

    def fake_claim():
        call_count["n"] += 1
        if call_count["n"] >= 1:
            w._running = False

    w._try_claim_and_dispatch = fake_claim

    t = threading.Thread(target=w._claim_loop)
    t.start()
    # Simulate the SUBSCRIBED callback firing on_new_pending
    w._notify_event.set()
    t.join(timeout=3)
    assert not t.is_alive(), "claim loop did not exit after notify"
    assert call_count["n"] == 1


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


def test_start_only_spawns_verifier_not_active_workers():
    """start() must NOT spin up the executor, claim loop, or Realtime
    subscription before the verifier confirms realtime is enabled —
    otherwise we'd be polling/subscribing for projects that don't
    support our feature at all."""
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct"
    dal.cluster = "cl"
    # Make is_realtime_enabled block forever so the verifier doesn't
    # progress; we want to inspect the state BEFORE verification completes.
    block_event = threading.Event()

    def blocking_check():
        block_event.wait(timeout=5)
        return None

    dal.is_realtime_enabled.side_effect = blocking_check
    config = MagicMock()
    chat_function = MagicMock()
    w = ConversationWorker(dal=dal, config=config, chat_function=chat_function)

    try:
        w.start()
        # Verifier thread is up.
        assert w._realtime_verify_thread is not None
        assert w._realtime_verify_thread.is_alive()
        # But active workers haven't been started.
        assert w._executor is None
        assert w._claim_thread is None
        assert w._realtime_manager is None
    finally:
        block_event.set()
        w.stop()


def test_start_starts_active_workers_after_definitive_true():
    """End-to-end: once is_realtime_enabled returns True, the verifier
    must call _start_active_workers and update HolmesStatus."""
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct"
    dal.cluster = "cl"
    dal.is_realtime_enabled.return_value = True
    config = MagicMock()
    chat_function = MagicMock()
    w = ConversationWorker(dal=dal, config=config, chat_function=chat_function)

    with patch(
        "holmes.core.conversations_worker.worker.CONVERSATION_WORKER_REALTIME_ENABLED",
        False,
    ), patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        try:
            w.start()
            assert w._realtime_verify_thread is not None
            w._realtime_verify_thread.join(timeout=3)
            assert not w._realtime_verify_thread.is_alive()
            mock_update.assert_called_once_with(dal, config, realtime_available=True)
            # Active workers should be up now.
            assert w._executor is not None
            assert w._claim_thread is not None
        finally:
            w.stop()


def test_start_does_not_start_active_workers_after_definitive_false():
    """When is_realtime_enabled returns False, the active workers must
    never spin up — start() never produced an executor or claim loop."""
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct"
    dal.cluster = "cl"
    dal.is_realtime_enabled.return_value = False
    config = MagicMock()
    chat_function = MagicMock()
    w = ConversationWorker(dal=dal, config=config, chat_function=chat_function)

    with patch(
        "holmes.core.conversations_worker.worker.update_holmes_status_in_db"
    ) as mock_update:
        w.start()
        assert w._realtime_verify_thread is not None
        w._realtime_verify_thread.join(timeout=3)
        assert not w._realtime_verify_thread.is_alive()

    # No HolmesStatus update from this path — the default-False row from
    # server startup already reflects reality.
    mock_update.assert_not_called()
    # And no polling/subscription components were ever created.
    assert w._executor is None
    assert w._claim_thread is None
    assert w._realtime_manager is None
    assert w._running is False  # stop() was triggered by the verifier


def test_start_skips_when_dal_disabled():
    """If the DAL itself isn't enabled, start() returns early without
    spawning any threads."""
    dal = MagicMock()
    dal.enabled = False
    config = MagicMock()
    chat_function = MagicMock()
    w = ConversationWorker(dal=dal, config=config, chat_function=chat_function)

    w.start()

    assert w._running is False
    assert w._realtime_verify_thread is None
    dal.is_realtime_enabled.assert_not_called()


def test_claim_loop_initial_claim_without_realtime():
    """When _realtime_manager is None, the claim loop does an immediate
    initial claim without waiting for a notification."""
    w = _bare_worker()
    w._realtime_manager = None

    call_count = {"n": 0}

    def fake_claim():
        call_count["n"] += 1
        w._running = False

    w._try_claim_and_dispatch = fake_claim

    t = threading.Thread(target=w._claim_loop)
    t.start()
    t.join(timeout=3)
    assert not t.is_alive()
    assert call_count["n"] == 1


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
