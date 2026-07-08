"""Tests for the Quickwit pod-logging toolset (unified fetch_pod_logs API)."""

from unittest.mock import MagicMock, patch

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.logging_utils.logging_api import FetchPodLogsParams
from holmes.plugins.toolsets.quickwit.quickwit import (
    QuickwitConfig,
    QuickwitLogsToolset,
)

TS = 1783432000  # base unix seconds for fixture hits


def _hit(t, container, msg, pod="app-1-abc", ns="prod"):
    return {
        "timestamp": t,
        "message": msg,
        "kubernetes": {
            "pod_name": pod,
            "pod_namespace": ns,
            "container_name": container,
        },
    }


def _response(hits):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"num_hits": len(hits), "hits": hits}
    resp.raise_for_status.return_value = None
    return resp


@pytest.fixture()
def toolset():
    ts = QuickwitLogsToolset()
    ts.config = QuickwitConfig(
        api_url="http://quickwit.monitoring.svc:7280",
        index="stage-zksync-os-stage",
    )
    return ts


def _fetch(toolset, hits, **param_overrides):
    kwargs = {"namespace": "prod", "pod_name": "app-1-abc", **param_overrides}
    params = FetchPodLogsParams(**kwargs)
    with patch(
        "holmes.plugins.toolsets.quickwit.quickwit.requests.get",
        return_value=_response(hits),
    ) as mock_get:
        result = toolset.fetch_pod_logs(params)
    return result, mock_get


def test_builds_exact_term_query_with_time_range(toolset):
    result, mock_get = _fetch(toolset, [_hit(TS, "server", "boom")])

    assert result.status == StructuredToolResultStatus.SUCCESS
    url = (
        mock_get.call_args.args[0]
        if mock_get.call_args.args
        else mock_get.call_args.kwargs["url"]
    )
    assert "/api/v1/stage-zksync-os-stage/search" in url
    q = mock_get.call_args.kwargs["params"]
    assert (
        q["query"] == "kubernetes.pod_namespace:prod AND kubernetes.pod_name:app-1-abc"
    )
    assert isinstance(q["start_timestamp"], int) and isinstance(q["end_timestamp"], int)
    assert q["end_timestamp"] > q["start_timestamp"]


def test_query_values_are_sanitized_against_injection(toolset):
    """Quotes/parens in identifiers must not reach the Quickwit query string."""
    result, mock_get = _fetch(toolset, [], namespace="prod'", pod_name='app OR *"')
    q = mock_get.call_args.kwargs["params"]["query"]
    assert "'" not in q and '"' not in q and "*" not in q
    # spaces are stripped, so the injected ` OR ` cannot become a query operator: the pod
    # value collapses into one harmless term and the query keeps exactly one AND
    assert " OR " not in q
    assert q == "kubernetes.pod_namespace:prod AND kubernetes.pod_name:appOR"


def test_log_lines_include_container_and_drop_empty_messages(toolset):
    hits = [
        _hit(TS + 1, "anvil", ""),  # noisy sidecar line with empty message → dropped
        _hit(TS + 2, "server", "CRITICAL: failed to reach http://127.0.0.1:19545"),
        _hit(TS + 3, "anvil", ""),
    ]
    result, _ = _fetch(toolset, hits)

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "19545" in result.data
    assert "server" in result.data
    assert result.data.count("\n") == 0  # single surviving line


def test_filter_and_exclude_filter_are_case_insensitive_regex(toolset):
    hits = [
        _hit(TS + 1, "server", "level=INFO healthy heartbeat"),
        _hit(TS + 2, "server", "level=ERROR something broke"),
        _hit(TS + 3, "server", "level=CRITICAL panic at provider"),
    ]
    result, _ = _fetch(
        toolset, hits, filter="error|critical", exclude_filter="HEARTBEAT"
    )

    assert "something broke" in result.data
    assert "panic at provider" in result.data
    assert "heartbeat" not in result.data


def test_invalid_filter_regex_falls_back_to_literal(toolset):
    hits = [_hit(TS, "server", "weird [unclosed bracket in log")]
    result, _ = _fetch(toolset, hits, filter="[unclosed")
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "unclosed bracket" in result.data


def test_limit_keeps_most_recent_lines_in_chronological_order(toolset):
    hits = [_hit(TS + i, "server", f"line-{i}") for i in range(10)]
    result, _ = _fetch(toolset, hits, limit=3)

    lines = result.data.strip().splitlines()
    assert "most recent of 10" in lines[0]  # the model is TOLD results were capped
    assert len(lines) == 4  # banner + 3 log lines
    assert (
        "line-7" in lines[1] and "line-9" in lines[3]
    )  # oldest→newest, most recent kept


def test_zero_hits_returns_no_data_with_context(toolset):
    result, _ = _fetch(toolset, [])
    assert result.status == StructuredToolResultStatus.NO_DATA
    assert "app-1-abc" in str(result.data)


def test_http_error_returns_error_result_never_raises(toolset):
    params = FetchPodLogsParams(namespace="prod", pod_name="app-1-abc")
    with patch(
        "holmes.plugins.toolsets.quickwit.quickwit.requests.get",
        side_effect=Exception("connection refused"),
    ):
        result = toolset.fetch_pod_logs(params)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "connection refused" in result.error


def test_prerequisites_fail_cleanly_without_config():
    ts = QuickwitLogsToolset()
    ok, msg = ts.prerequisites_callable({})
    assert ok is False and msg
