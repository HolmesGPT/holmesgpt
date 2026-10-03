"""Tests for the Quickwit pod-logging toolset (unified fetch_pod_logs API)."""

from urllib.parse import parse_qs, urlparse

import pytest
import responses

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.logging_utils.logging_api import FetchPodLogsParams
from holmes.plugins.toolsets.quickwit.quickwit import (
    QuickwitConfig,
    QuickwitLogsToolset,
)

TS = 1783432000  # base unix seconds for fixture hits
API_URL = "http://quickwit.monitoring.svc:7280"
SEARCH_URL = f"{API_URL}/api/v1/stage-zksync-os-stage/search"


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


@pytest.fixture()
def toolset():
    ts = QuickwitLogsToolset()
    ts.config = QuickwitConfig(api_url=API_URL, index="stage-zksync-os-stage")
    return ts


def _fetch(toolset, hits, **param_overrides):
    """Run fetch_pod_logs against a mocked Quickwit; returns (result, sent query params)."""
    kwargs = {"namespace": "prod", "pod_name": "app-1-abc", **param_overrides}
    params = FetchPodLogsParams(**kwargs)
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            SEARCH_URL,
            json={"num_hits": len(hits), "hits": hits},
            status=200,
        )
        result = toolset.fetch_pod_logs(params)
        url = urlparse(rsps.calls[0].request.url)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
    return result, query


def test_builds_exact_term_query_with_time_range(toolset):
    result, query = _fetch(toolset, [_hit(TS, "server", "boom")])

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert (
        query["query"]
        == "kubernetes.pod_namespace:prod AND kubernetes.pod_name:app-1-abc"
    )
    assert int(query["end_timestamp"]) > int(query["start_timestamp"])
    assert query["sort_by"] == "-timestamp"


def test_query_values_are_sanitized_against_injection(toolset):
    """Quotes/spaces/wildcards in identifiers must not reach the Quickwit query string."""
    result, query = _fetch(toolset, [], namespace="prod'", pod_name='app OR *"')

    q = query["query"]
    assert "'" not in q and '"' not in q and "*" not in q
    # spaces are stripped, so the injected ` OR ` cannot become a query operator: the pod
    # value collapses into one harmless term and the query keeps exactly one AND
    assert " OR " not in q
    assert q == "kubernetes.pod_namespace:prod AND kubernetes.pod_name:appOR"


def test_log_lines_include_container_and_drop_empty_messages(toolset):
    hits = [
        _hit(TS + 1, "anvil", ""),  # noisy sidecar line with empty message -> dropped
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
    assert "line-7" in lines[1] and "line-9" in lines[3]  # oldest->newest, recent kept


def test_zero_hits_returns_no_data_with_context(toolset):
    result, _ = _fetch(toolset, [])
    assert result.status == StructuredToolResultStatus.NO_DATA
    assert "app-1-abc" in str(result.data)


def test_http_error_surfaces_status_and_response_body(toolset):
    """Quickwit puts the actionable detail (e.g. malformed query, unknown field) in the
    response body — the error result must include it for LLM self-correction."""
    params = FetchPodLogsParams(namespace="prod", pod_name="app-1-abc")
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            SEARCH_URL,
            body='{"message": "field does not exist: `kubernetes.pod_nam`"}',
            status=400,
        )
        result = toolset.fetch_pod_logs(params)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "HTTP 400" in result.error
    assert "field does not exist" in result.error


def test_connection_error_returns_error_result_never_raises(toolset):
    params = FetchPodLogsParams(namespace="prod", pod_name="app-1-abc")
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            SEARCH_URL,
            body=ConnectionError("connection refused"),
        )
        result = toolset.fetch_pod_logs(params)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "connection refused" in result.error


def test_non_dict_response_returns_error_not_attributeerror(toolset):
    """A 200 with a JSON array body must produce a clean ERROR result."""
    params = FetchPodLogsParams(namespace="prod", pod_name="app-1-abc")
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, SEARCH_URL, json=["unexpected", "array"], status=200)
        result = toolset.fetch_pod_logs(params)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "unexpected response shape" in result.error


def test_prerequisites_fail_cleanly_without_config():
    ts = QuickwitLogsToolset()
    ok, msg = ts.prerequisites_callable({})
    assert ok is False and msg


def test_health_check_error_includes_response_body():
    ts = QuickwitLogsToolset()
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            f"{API_URL}/health/livez",
            body="service unavailable: searcher not ready",
            status=503,
        )
        ok, msg = ts.prerequisites_callable(
            {"api_url": API_URL, "index": "stage-zksync-os-stage"}
        )
    assert ok is False
    assert "HTTP 503" in msg
    assert "searcher not ready" in msg
