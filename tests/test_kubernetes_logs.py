import pytest
from unittest.mock import MagicMock, patch
from holmes.plugins.toolsets.kubernetes_logs import (
    KubernetesLogsToolset,
    LogResult,
    StructuredLog,
    parse_logs,
    filter_logs,
    format_logs,
)
from holmes.plugins.toolsets.logging_utils.logging_api import FetchPodLogsParams


# ── parse_logs ────────────────────────────────────────────────────────────────

def test_parse_logs_with_timestamp():
    line = "2024-01-15T10:30:00Z OOMKilled: container exceeded memory limit"
    result = parse_logs(line, "app")
    assert len(result) == 1
    assert result[0].timestamp_ms is not None
    assert "OOMKilled" in result[0].content
    assert result[0].container == "app"


def test_parse_logs_no_timestamp():
    line = "plain log line with no timestamp"
    result = parse_logs(line, "app")
    assert len(result) == 1
    assert result[0].timestamp_ms is None
    assert result[0].content == line


def test_parse_logs_multiline_stacktrace():
    """Stacktraces have no timestamp on continuation lines — they should
    be appended to the previous log entry, not treated as new entries."""
    logs = (
        "2024-01-15T10:30:00Z Exception in thread main\n"
        "    at com.example.App.main(App.java:10)\n"
        "    at com.example.App.run(App.java:20)"
    )
    result = parse_logs(logs, "app")
    assert len(result) == 1
    assert "Exception" in result[0].content
    assert "App.java:10" in result[0].content


def test_parse_logs_empty():
    result = parse_logs("", "app")
    assert result == []


def test_parse_logs_none():
    result = parse_logs(None, "app")
    assert result == []


# ── filter_logs ───────────────────────────────────────────────────────────────

def _make_params(**kwargs):
    defaults = dict(
        pod_name="test-pod",
        namespace="default",
        start_time=None,
        end_time=None,
        filter=None,
        exclude_filter=None,
        limit=None,
    )
    defaults.update(kwargs)
    return FetchPodLogsParams(**defaults)


def _make_log(content, ts_ms=None, container="app"):
    return StructuredLog(timestamp_ms=ts_ms, content=content, container=container)


def test_filter_logs_no_filters():
    logs = [_make_log("error: OOMKilled"), _make_log("info: pod started")]
    params = _make_params()
    result, count, *_ = filter_logs(logs, params)
    assert len(result) == 2
    assert count == 2


def test_filter_logs_include_filter_regex():
    logs = [
        _make_log("OOMKilled: memory limit exceeded"),
        _make_log("info: container started"),
        _make_log("OOMKilled: again"),
    ]
    params = _make_params(filter="OOMKilled")
    result, count, *_ = filter_logs(logs, params)
    assert len(result) == 2
    assert all("OOMKilled" in l.content for l in result)


def test_filter_logs_exclude_filter():
    logs = [
        _make_log("ERROR: CrashLoopBackOff detected"),
        _make_log("DEBUG: health check ok"),
        _make_log("ERROR: ImagePullBackOff"),
    ]
    params = _make_params(exclude_filter="DEBUG")
    result, *_ = filter_logs(logs, params)
    assert len(result) == 2
    assert all("DEBUG" not in l.content for l in result)


def test_filter_logs_limit():
    logs = [_make_log(f"log line {i}", ts_ms=i) for i in range(10)]
    params = _make_params(limit=3)
    result, count, *_ = filter_logs(logs, params)
    # limit returns the LAST N logs
    assert len(result) == 3
    assert count == 10


def test_filter_logs_invalid_regex_falls_back_to_substring():
    logs = [
        _make_log("CrashLoopBackOff: back-off restarting"),
        _make_log("info: all good"),
    ]
    # "[invalid" is not valid regex — should fall back to substring match
    params = _make_params(filter="[CrashLoop")
    result, _, used_fallback, *_ = filter_logs(logs, params)
    assert used_fallback is True


def test_filter_logs_time_range():
    logs = [
        _make_log("too old", ts_ms=1000),
        _make_log("in range", ts_ms=5000),
        _make_log("too new", ts_ms=9000),
    ]
    params = _make_params(
        start_time="1970-01-01T00:00:03Z",
        end_time="1970-01-01T00:00:07Z",
    )
    result, *_ = filter_logs(logs, params)
    assert len(result) == 1
    assert result[0].content == "in range"


# ── format_logs ───────────────────────────────────────────────────────────────

def test_format_logs_single_container():
    logs = [
        StructuredLog(timestamp_ms=None, content="pod started", container="app"),
        StructuredLog(timestamp_ms=None, content="OOMKilled", container="app"),
    ]
    output = format_logs(logs, display_container_name=False)
    assert "pod started" in output
    assert "app:" not in output  # container name should be hidden


def test_format_logs_multiple_containers():
    logs = [
        StructuredLog(timestamp_ms=None, content="starting", container="app"),
        StructuredLog(timestamp_ms=None, content="sidecar ready", container="sidecar"),
    ]
    output = format_logs(logs, display_container_name=True)
    assert "app: starting" in output
    assert "sidecar: sidecar ready" in output


# ── _parse_kubectl_logs ───────────────────────────────────────────────────────

@patch("subprocess.run")
def test_parse_kubectl_logs_single_container(mock_run):
    raw = "[mypod/app] 2024-01-15T10:30:00Z pod initialising\n"
    toolset = _make_toolset()
    result = toolset._parse_kubectl_logs(raw)
    assert len(result.logs) == 1
    assert result.logs[0].container == "app"
    assert "pod initialising" in result.logs[0].content
    assert result.has_multiple_containers is False


@patch("subprocess.run")
def test_parse_kubectl_logs_multiple_containers(mock_run):
    raw = (
        "[mypod/app] 2024-01-15T10:30:00Z app log\n"
        "[mypod/sidecar] 2024-01-15T10:30:01Z sidecar log\n"
    )
    toolset = _make_toolset()
    result = toolset._parse_kubectl_logs(raw)
    assert result.has_multiple_containers is True
    containers = {l.container for l in result.logs}
    assert containers == {"app", "sidecar"}


@patch("subprocess.run")
def test_parse_kubectl_logs_oomkill_signals(mock_run):
    """OOMKilled shows exitCode:137 in logs — make sure it is captured."""
    raw = "[mypod/app] 2024-01-15T10:30:00Z OOMKilled exitCode:137 memory limit exceeded\n"
    toolset = _make_toolset()
    result = toolset._parse_kubectl_logs(raw)
    assert any("OOMKilled" in l.content for l in result.logs)


@patch("subprocess.run")
def test_parse_kubectl_logs_empty(mock_run):
    toolset = _make_toolset()
    result = toolset._parse_kubectl_logs("")
    assert result.logs == []
    assert result.has_multiple_containers is False


# ── health_check ──────────────────────────────────────────────────────────────

@patch("subprocess.run")
def test_health_check_kubectl_available(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stderr="")
    toolset = _make_toolset()
    enabled, reason = toolset.health_check()
    assert enabled is True
    assert reason == ""


@patch("subprocess.run")
def test_health_check_kubectl_missing(mock_run):
    mock_run.side_effect = FileNotFoundError()
    toolset = _make_toolset()
    enabled, reason = toolset.health_check()
    assert enabled is False
    assert "not found" in reason.lower()


@patch("subprocess.run")
def test_health_check_kubectl_timeout(mock_run):
    import subprocess
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="kubectl", timeout=10)
    toolset = _make_toolset()
    enabled, reason = toolset.health_check()
    assert enabled is False
    assert "timed out" in reason.lower()


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_toolset():
    """Build a toolset without triggering a real kubectl health check."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        return KubernetesLogsToolset()
