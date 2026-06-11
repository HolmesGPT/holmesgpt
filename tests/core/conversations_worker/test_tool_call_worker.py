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
    tool = MagicMock(name="tool", spec=["name", "_is_restricted", "_get_approval_requirement", "invoke"])
    tool.name = "probe"
    tool._is_restricted.return_value = False
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
