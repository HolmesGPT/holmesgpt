from unittest.mock import Mock, patch

import pytest

from holmes.core.tools import ToolInvokeContext
from tests.conftest import MockLLM
from holmes.plugins.toolsets.prometheus.prometheus import (
    ExecuteInstantQuery,
    ExecuteRangeQuery,
    PrometheusConfig,
    PrometheusToolset,
)

MODULE = "holmes.plugins.toolsets.prometheus.prometheus"


def make_toolset() -> PrometheusToolset:
    toolset = PrometheusToolset()
    toolset.config = PrometheusConfig(prometheus_url="http://prometheus:9090")
    return toolset


def make_context() -> ToolInvokeContext:
    return ToolInvokeContext(
        llm=MockLLM(),
        max_token_count=1_000_000,
        tool_call_id="call-1",
        tool_name="execute_prometheus_query",
    )


def ok_response() -> Mock:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "1"]}]},
    }
    return response


RANGE_PARAMS = {
    "start": "2024-01-01T00:00:00Z",
    "end": "2024-01-01T01:00:00Z",
    "step": "60s",
}


@pytest.mark.parametrize(
    "tool_cls,extra_params",
    [
        (ExecuteInstantQuery, {}),
        (ExecuteRangeQuery, RANGE_PARAMS),
    ],
)
def test_explicit_null_timeout_falls_back_to_default(tool_cls, extra_params):
    """The model may emit "timeout": null for an optional param; that must not
    raise TypeError (None > int) — the default should apply. Issue #2376."""
    toolset = make_toolset()
    tool = tool_cls(toolset)
    params = {"query": "up", "timeout": None, **extra_params}

    with patch(f"{MODULE}.do_request", return_value=ok_response()) as request:
        tool._invoke(params, make_context())

    assert request.called
    assert (
        request.call_args.kwargs["timeout"]
        == toolset.config.query_timeout_seconds_default
    )


@pytest.mark.parametrize(
    "tool_cls,extra_params",
    [
        (ExecuteInstantQuery, {}),
        (ExecuteRangeQuery, RANGE_PARAMS),
    ],
)
def test_explicit_timeout_is_honored(tool_cls, extra_params):
    toolset = make_toolset()
    tool = tool_cls(toolset)
    params = {"query": "up", "timeout": 42, **extra_params}

    with patch(f"{MODULE}.do_request", return_value=ok_response()) as request:
        tool._invoke(params, make_context())

    assert request.call_args.kwargs["timeout"] == 42


@pytest.mark.parametrize(
    "tool_cls,extra_params",
    [
        (ExecuteInstantQuery, {}),
        (ExecuteRangeQuery, RANGE_PARAMS),
    ],
)
def test_timeout_above_hard_max_is_clamped(tool_cls, extra_params):
    toolset = make_toolset()
    tool = tool_cls(toolset)
    params = {"query": "up", "timeout": 10**9, **extra_params}

    with patch(f"{MODULE}.do_request", return_value=ok_response()) as request:
        tool._invoke(params, make_context())

    assert (
        request.call_args.kwargs["timeout"]
        == toolset.config.query_timeout_seconds_hard_max
    )
