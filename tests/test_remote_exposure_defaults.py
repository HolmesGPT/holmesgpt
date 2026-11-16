import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.conversations_worker.tool_call_worker import ToolCallWorker
from holmes.core.llm import LLM
from holmes.core.tools import StructuredToolResultStatus, ToolsetStatusEnum
from holmes.core.toolset_manager import ToolsetManager
from holmes.plugins.toolsets import load_builtin_toolsets
from holmes.plugins.toolsets.kubernetes_logs import KubernetesLogsToolset
from holmes.utils.holmes_sync_toolsets import build_remote_tools_meta
from holmes.version import get_version

# Toolsets that run only inside their own cluster, so other clusters need them
# remotely. Must match the table in docs/data-sources/cross-cluster-tools.md.
DEFAULT_EXPOSED = {
    "bash",
    "kubernetes/core",
    "kubernetes/kube-prometheus-stack",
    "kubernetes/live-metrics",
    "kubernetes/logs",
    "prometheus/metrics",
}

NEWLY_DEFAULT_EXPOSED = [
    "kubernetes/logs",
    "kubernetes/live-metrics",
    "kubernetes/kube-prometheus-stack",
]

DOCS_PAGE = (
    Path(__file__).parent.parent / "docs" / "data-sources" / "cross-cluster-tools.md"
)


def _exposed_builtin_names(legacy_logs: bool) -> set:
    with patch("holmes.plugins.toolsets.USE_LEGACY_KUBERNETES_LOGS", legacy_logs):
        toolsets = load_builtin_toolsets()
    return {t.name for t in toolsets if t.expose_remotely and not t.is_core}


@pytest.mark.parametrize("legacy_logs", [False, True], ids=["python", "yaml"])
def test_default_exposed_builtin_toolsets(legacy_logs):
    assert _exposed_builtin_names(legacy_logs) == DEFAULT_EXPOSED


def test_docs_table_lists_the_default_exposed_toolsets():
    rows = re.findall(r"^\| `([^`]+)` \| ✅", DOCS_PAGE.read_text(), re.MULTILINE)
    assert set(rows) == DEFAULT_EXPOSED


def _merged_toolset(name: str, config: dict):
    manager = ToolsetManager(toolsets={name: config})
    toolsets = manager._list_all_toolsets(check_prerequisites=False)
    return next(t for t in toolsets if t.name == name)


@pytest.mark.parametrize("name", NEWLY_DEFAULT_EXPOSED)
def test_enabling_a_toolset_keeps_its_default_exposure(name):
    assert _merged_toolset(name, {"enabled": True}).expose_remotely is True


@pytest.mark.parametrize("name", NEWLY_DEFAULT_EXPOSED)
def test_user_can_opt_a_default_exposed_toolset_out(name):
    toolset = _merged_toolset(name, {"enabled": True, "expose_remotely": False})
    assert toolset.expose_remotely is False


def _enabled_logs_toolset() -> KubernetesLogsToolset:
    toolset = KubernetesLogsToolset()
    toolset.status = ToolsetStatusEnum.ENABLED
    return toolset


def test_kubernetes_logs_publishes_fetch_pod_logs():
    meta = build_remote_tools_meta(_enabled_logs_toolset())

    assert meta is not None
    assert [t["function"]["name"] for t in meta["tools"]] == ["fetch_pod_logs"]


def test_kubernetes_logs_opted_out_publishes_nothing():
    toolset = _enabled_logs_toolset()
    toolset.expose_remotely = False

    assert build_remote_tools_meta(toolset) is None


def _worker_for(toolset) -> ToolCallWorker:
    executor = MagicMock()
    executor.tools_by_name = {t.name: t for t in toolset.tools}
    executor._tool_to_toolset = {t.name: toolset for t in toolset.tools}
    config = MagicMock()
    config.create_server_tool_executor.return_value = executor
    llm = MagicMock(spec=LLM)
    llm.count_tokens.return_value = MagicMock(total_tokens=10)
    config._get_llm.return_value = llm
    return ToolCallWorker(dal=MagicMock(), config=config, holmes_id="h-test")


def _fetch_logs_row() -> dict:
    return {
        "id": "row-1",
        "user_id": None,
        "tool_request": {
            "tool_name": "fetch_pod_logs",
            "tool_params": {"namespace": "app-1", "pod_name": "checkout-7d9f"},
            "tool_call_id": "call-1",
            "max_token_count": 16000,
        },
        "metadata": {"source_version": get_version()},
    }


def _fake_kubectl(cmd, **kwargs):
    if "--previous" in cmd:
        return subprocess.CompletedProcess(
            cmd, 1, stdout="previous terminated container not found"
        )
    return subprocess.CompletedProcess(
        cmd,
        0,
        stdout="[pod/checkout-7d9f/app] 2026-11-15T09:00:00.000000000Z "
        "payment gateway timeout\n",
    )


def test_remote_call_reads_pod_logs_on_the_executor():
    worker = _worker_for(_enabled_logs_toolset())

    with patch(
        "holmes.plugins.toolsets.kubernetes_logs.subprocess.run",
        side_effect=_fake_kubectl,
    ) as run:
        resp = worker._execute(_fetch_logs_row())

    assert resp["status"] == StructuredToolResultStatus.SUCCESS.value, resp
    assert "payment gateway timeout" in resp["data"]
    kubectl_logs = ["kubectl", "logs", "checkout-7d9f", "-n"]
    assert any(c.args[0][:4] == kubectl_logs for c in run.call_args_list)


def test_remote_call_is_refused_when_logs_opted_out():
    toolset = _enabled_logs_toolset()
    toolset.expose_remotely = False
    worker = _worker_for(toolset)

    with patch.object(KubernetesLogsToolset, "fetch_pod_logs") as fetch:
        resp = worker._execute(_fetch_logs_row())

    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "'kubernetes/logs' is not exposed for remote execution" in resp["error"]
    fetch.assert_not_called()
