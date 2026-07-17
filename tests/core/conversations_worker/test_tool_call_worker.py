"""Unit tests for the remote tool-call executor logic that the live e2e
harness can't deterministically exercise: result serialization (cap +
compression), the RemoteCallerLLM-free ToolInvokeContext build, and — most
importantly — multi-instance resolution (the path that was dead before
`remote_exposed_instances` was implemented).
"""

import base64
import gzip
import random
import string
import threading
from typing import Optional
from unittest.mock import MagicMock, patch

from holmes.core.conversations_worker.realtime_manager import RealtimeWorker
from holmes.core.conversations_worker.tool_call_worker import (
    ToolCallWorker,
    serialize_tool_response,
)
from holmes.core.llm import LLM
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Toolset,
    ToolsetStatusEnum,
)
from holmes.plugins.toolsets.multi_instance import MultiInstanceToolset
from holmes.plugins.toolsets.prometheus.prometheus import PrometheusToolset
from holmes.version import get_version


# ---- result serialization ----


def _ok(data):
    return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data=data)


def test_small_result_passthrough():
    p = serialize_tool_response(_ok("hello"), 0.1)
    assert p["data"] == "hello" and not p["compressed"] and p["data_gz_b64"] is None


def test_medium_result_is_gzipped():
    big = "x" * 200_000
    p = serialize_tool_response(_ok(big), 0.1)
    assert p["compressed"] and p["data"] is None
    assert gzip.decompress(base64.b64decode(p["data_gz_b64"])).decode() == big


def test_oversized_result_rejected():
    p = serialize_tool_response(_ok("y" * 2_000_000), 0.1)
    assert p["status"] == StructuredToolResultStatus.ERROR.value
    assert "too large" in p["error"] and "narrow the query" in p["error"]
    assert p["data"] is None


def test_compress_boundary_uses_chars_not_bytes():
    # Just over the threshold by chars triggers compression.
    p = serialize_tool_response(_ok("z" * 100_001), 0.1, compress_threshold=100_000)
    assert p["compressed"]


def test_incompressible_result_stays_plain():
    # Random printable text (~6.6 bits/char entropy): gzip can't beat the
    # +33% base64 overhead, so the payload must stay uncompressed.
    rng = random.Random(310)
    noise = "".join(rng.choices(string.printable, k=120_000))
    p = serialize_tool_response(_ok(noise), 0.1, compress_threshold=100_000)
    assert not p["compressed"] and p["data_gz_b64"] is None
    assert p["data"] == noise


# ---- multi-instance resolution in _execute ----


def _make_tool(instance_echo=True):
    tool = MagicMock(name="tool", spec=["name", "_get_approval_requirement", "invoke"])
    tool.name = "probe"
    tool._get_approval_requirement.return_value = None

    def _invoke(params, context):
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=f"ran on instance={params.get('instance')}",
        )

    tool.invoke.side_effect = _invoke
    return tool


def _worker_with_tool(exposed_instances: Optional[list], is_core=False):
    """ToolCallWorker whose tool_executor resolves one exposed toolset/tool,
    with the given remote_exposed_instances() result (None = the method is
    absent, i.e. a non-multi-instance toolset)."""
    tool = _make_tool()

    spec = ["name", "is_core", "expose_remotely", "status"]
    if exposed_instances is not None:
        spec.append("remote_exposed_instances")
    toolset = MagicMock(name="toolset", spec=spec)
    toolset.name = "fake_ts"
    toolset.is_core = is_core
    toolset.expose_remotely = True
    toolset.status = ToolsetStatusEnum.ENABLED
    if exposed_instances is not None:
        toolset.remote_exposed_instances.return_value = exposed_instances

    executor = MagicMock()
    executor.tools_by_name = {"probe": tool}
    executor._tool_to_toolset = {"probe": toolset}

    config = MagicMock()
    config.create_tool_executor.return_value = executor
    config._get_llm.return_value = MagicMock(spec=LLM)

    return ToolCallWorker(dal=MagicMock(), config=config, holmes_id="h-test")


def _row(instance=None, version=None):
    return {
        "id": "row-1",
        "user_id": None,
        "tool_request": {
            "tool_name": "probe",
            "tool_params": {},
            "instance": instance,
            "tool_call_id": "call-1",
            "max_token_count": 16000,
        },
        "metadata": {"source_version": version or get_version()},
    }


def test_instance_omitted_single_exposed_defaults():
    worker = _worker_with_tool(["only"])
    resp = worker._execute(_row(instance=None))
    assert resp["status"] == StructuredToolResultStatus.SUCCESS.value
    assert "instance=only" in resp["data"]


def test_instance_omitted_multiple_exposed_errors_with_list():
    worker = _worker_with_tool(["team-a", "team-b"])
    resp = worker._execute(_row(instance=None))
    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "team-a" in resp["error"] and "team-b" in resp["error"]


def test_instance_given_must_be_exposed():
    worker = _worker_with_tool(["team-a", "team-b"])
    resp = worker._execute(_row(instance="ghost"))
    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "not exposed" in resp["error"]


def test_instance_given_valid_routes():
    worker = _worker_with_tool(["team-a", "team-b"])
    resp = worker._execute(_row(instance="team-b"))
    assert resp["status"] == StructuredToolResultStatus.SUCCESS.value
    assert "instance=team-b" in resp["data"]


def test_instance_on_non_instance_toolset_errors():
    worker = _worker_with_tool(None)  # no remote_exposed_instances method
    resp = worker._execute(_row(instance="x"))
    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "does not support instances" in resp["error"]


def test_version_mismatch_rejected():
    worker = _worker_with_tool(None)
    resp = worker._execute(_row(version="0.0.0-different"))
    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "version mismatch" in resp["error"]


def test_is_core_toolset_rejected():
    worker = _worker_with_tool(None, is_core=True)
    resp = worker._execute(_row())
    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "cannot run remotely" in resp["error"]


# ---- multi_instance.remote_exposed_instances heuristic resolution ----


def test_multi_instance_exposed_filters_by_locality():
    wrapper = MultiInstanceToolset(PrometheusToolset)
    # Two healthy instances post-prerequisite: one in-cluster, one external SaaS.
    wrapper._children = {"local": PrometheusToolset(), "saas": PrometheusToolset()}
    wrapper._instance_configs = {
        "local": {"prometheus_url": "http://prometheus.monitoring.svc:9090"},
        "saas": {"prometheus_url": "https://prometheus.grafana.net"},
    }
    assert wrapper.remote_exposed_instances() == ["local"]


def test_prometheus_single_instance_locality_narrows_exposure():
    """Unwrapped (single-instance) prometheus must apply the locality
    heuristic in prerequisites: SaaS URL => not exposed; in-cluster => exposed."""
    saas = PrometheusToolset()
    with patch.object(PrometheusToolset, "_is_healthy", return_value=(True, "")):
        saas.prerequisites_callable({"prometheus_url": "https://prometheus.grafana.net"})
        assert saas.expose_remotely is False

        local = PrometheusToolset()
        local.prerequisites_callable(
            {"prometheus_url": "http://prometheus.monitoring.svc:9090"}
        )
        assert local.expose_remotely is True


# ---- bounded claiming (free pool capacity) ----


def _claimable_worker(monkeypatch, max_concurrent=10):
    """A ToolCallWorker wired with a mock pool/dal so _try_claim_and_dispatch
    and _execute_safe can be driven without real threads."""
    monkeypatch.setattr(
        "holmes.core.conversations_worker.tool_call_worker.TOOL_CALLER_MAX_CONCURRENT",
        max_concurrent,
    )
    worker = ToolCallWorker(dal=MagicMock(), config=MagicMock(), holmes_id="h-test")
    worker._running = True
    worker._pool = MagicMock()
    return worker


def test_tool_calls_claim_only_free_slots(monkeypatch):
    worker = _claimable_worker(monkeypatch, max_concurrent=10)
    worker.dal.claim_n_pending_tool_calls.return_value = [{"id": "t1"}, {"id": "t2"}]
    worker._try_claim_and_dispatch()
    # No free work yet -> asks for the full pool size.
    worker.dal.claim_n_pending_tool_calls.assert_called_once_with("h-test", 10)
    assert worker._pool.submit.call_count == 2
    # Both submitted rows count against capacity.
    assert worker._active_count == 2


def test_tool_calls_limit_reflects_in_flight(monkeypatch):
    worker = _claimable_worker(monkeypatch, max_concurrent=10)
    worker._active_count = 7  # 7 already running -> 3 free
    worker.dal.claim_n_pending_tool_calls.return_value = []
    worker._try_claim_and_dispatch()
    worker.dal.claim_n_pending_tool_calls.assert_called_once_with("h-test", 3)


def test_tool_calls_skip_claim_when_at_capacity(monkeypatch):
    worker = _claimable_worker(monkeypatch, max_concurrent=2)
    worker._active_count = 2  # full
    worker._try_claim_and_dispatch()
    worker.dal.claim_n_pending_tool_calls.assert_not_called()
    worker._pool.submit.assert_not_called()


def test_tool_call_submit_failure_decrements_active(monkeypatch):
    worker = _claimable_worker(monkeypatch, max_concurrent=10)
    worker.dal.claim_n_pending_tool_calls.return_value = [{"id": "t1"}]
    worker._pool.submit.side_effect = RuntimeError("pool shut down")
    worker._try_claim_and_dispatch()
    # The failed submit must not leak a slot.
    assert worker._active_count == 0


def test_execute_safe_frees_slot_and_wakes_claim_loop(monkeypatch):
    worker = _claimable_worker(monkeypatch, max_concurrent=10)
    worker._active_count = 1
    worker._notify_event.clear()
    worker.dal.post_remote_tool_call_result.return_value = True
    with patch.object(ToolCallWorker, "_execute", lambda self, row: {"status": "SUCCESS"}):
        worker._execute_safe({"id": "t1"})
    assert worker._active_count == 0
    assert worker._notify_event.is_set()


def test_backlog_drains_with_exact_claim_calls_and_limits(monkeypatch):
    """Exact-accounting test: draining a 12-row backlog at capacity 5 must

      * call claim exactly once per iteration that has free capacity,
      * pass limit == free slots on every call (never more),
      * dispatch exactly the rows it claimed — each tool call once, never
        exceeding TOOL_CALLER_MAX_CONCURRENT — so no work is missed or
        double-claimed,
      * issue ceil(12/5) == 3 claim calls total, then a final empty claim.
    """
    worker = _claimable_worker(monkeypatch, max_concurrent=5)

    pending = [f"t{i}" for i in range(12)]

    def fake_claim(_holmes_id, limit):
        assert limit > 0  # the worker must never call claim with no free capacity
        batch, pending[:] = pending[:limit], pending[limit:]
        return [{"id": tid} for tid in batch]

    worker.dal.claim_n_pending_tool_calls.side_effect = fake_claim

    dispatched: list = []
    worker._pool.submit.side_effect = lambda _fn, row: dispatched.append(row["id"])

    observed_limits = []
    dispatched_per_iter = []
    max_active = 0
    # Each iteration: claim+dispatch, then simulate every running tool call
    # finishing (frees the whole pool for the next claim), until drained.
    while pending or worker._active_count:
        free_before = 5 - worker._active_count
        before = worker.dal.claim_n_pending_tool_calls.call_count
        dispatched_before = len(dispatched)
        worker._try_claim_and_dispatch()
        after = worker.dal.claim_n_pending_tool_calls.call_count

        assert after == before + 1, "exactly one claim call per iteration"
        limit = worker.dal.claim_n_pending_tool_calls.call_args.args[1]
        assert limit == free_before, "claim limit must equal free capacity"
        observed_limits.append(limit)
        dispatched_per_iter.append(len(dispatched) - dispatched_before)

        max_active = max(max_active, worker._active_count)
        assert worker._active_count <= 5, "never exceed TOOL_CALLER_MAX_CONCURRENT"

        worker._active_count = 0  # every dispatched tool call finishes

    # The whole pool is freed each iteration, so every claim requests the full
    # 5 free slots; the final batch simply returns fewer rows (the remaining 2).
    assert observed_limits == [5, 5, 5]
    assert dispatched_per_iter == [5, 5, 2]  # ceil(12/5): 5, 5, then 2
    assert max_active == 5
    # Every tool call dispatched exactly once — none missed, none duplicated.
    assert sorted(dispatched) == sorted(f"t{i}" for i in range(12))

    # Backlog drained: one more pass issues a claim that returns nothing and
    # dispatches nothing (no phantom work).
    calls_before = worker.dal.claim_n_pending_tool_calls.call_count
    worker._try_claim_and_dispatch()
    assert worker.dal.claim_n_pending_tool_calls.call_count == calls_before + 1
    assert len(dispatched) == 12


def test_two_tool_workers_claim_disjoint_sets(monkeypatch):
    """Cross-instance load balancing for tool calls: two workers draining the
    SAME backlog must never both dispatch the same row. Simulates the DB's
    FOR UPDATE SKIP LOCKED (each claim atomically takes a disjoint slice) and
    asserts no double-dispatch, every row handled once, both workers active."""
    pending = [f"t{i}" for i in range(12)]
    db_lock = threading.Lock()

    def fake_claim(_holmes_id, limit):
        with db_lock:
            batch, pending[:] = pending[:limit], pending[limit:]
        return [{"id": tid} for tid in batch]

    dispatched: dict = {}  # id -> worker label (detects double dispatch)

    def make_worker(label):
        w = _claimable_worker(monkeypatch, max_concurrent=5)
        w.dal.claim_n_pending_tool_calls.side_effect = fake_claim

        def record(_fn, row, _label=label):
            assert row["id"] not in dispatched, (
                f"{row['id']} dispatched twice (by "
                f"{dispatched.get(row['id'])} and {_label})"
            )
            dispatched[row["id"]] = _label

        w._pool.submit.side_effect = record
        return w

    w1 = make_worker("w1")
    w2 = make_worker("w2")

    while pending or w1._active_count or w2._active_count:
        for w in (w1, w2):
            w._try_claim_and_dispatch()
            w._active_count = 0  # whole pool frees after each claim

    assert sorted(dispatched) == sorted(f"t{i}" for i in range(12))
    assert "w1" in dispatched.values() and "w2" in dispatched.values(), (
        "backlog exceeded one pool, so both workers should have claimed some"
    )


def test_signal_arriving_during_claim_is_not_lost(monkeypatch):
    """The tool-call claim loop clears _notify_event before claiming, so a
    'pending_tool_calls' broadcast that lands mid-claim re-sets the event and
    the next wait() wakes immediately — the wakeup is never lost."""
    worker = _claimable_worker(monkeypatch, max_concurrent=5)

    def claim_then_broadcast(_holmes_id, _limit):
        worker.claim_pending_tool_calls()  # == _notify_event.set()
        return []

    worker.dal.claim_n_pending_tool_calls.side_effect = claim_then_broadcast

    worker._notify_event.clear()
    worker._try_claim_and_dispatch()
    assert worker._notify_event.is_set()
    assert worker._notify_event.wait(timeout=0) is True


# ---- _wake_all routes to both workers ----


def test_realtime_worker_wake_all_fires_both():
    pending = MagicMock()
    tool_calls = MagicMock()
    rw = RealtimeWorker(
        dal=MagicMock(),
        holmes_id="h",
        on_new_pending=pending,
        on_new_tool_calls=tool_calls,
    )
    rw._wake_all()
    pending.assert_called_once()
    tool_calls.assert_called_once()


def test_realtime_worker_wake_all_tolerates_no_tool_worker():
    pending = MagicMock()
    rw = RealtimeWorker(dal=MagicMock(), holmes_id="h", on_new_pending=pending)
    rw._wake_all()  # must not raise when on_new_tool_calls is None
    pending.assert_called_once()
