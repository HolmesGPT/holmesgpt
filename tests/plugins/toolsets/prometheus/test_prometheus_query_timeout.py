"""Timeout handling of the Prometheus query tools.

Strict tool calls list every optional parameter as required and nullable, so
models send ``"timeout": null`` when they don't want to set one. That must fall
back to the configured default instead of failing the query.
"""

import pytest
import responses

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.prometheus.prometheus import (
    ExecuteInstantQuery,
    ExecuteRangeQuery,
    PrometheusConfig,
    PrometheusToolset,
)
from tests.conftest import create_mock_tool_invoke_context

BASE_URL = "http://prometheus.test:9090/"
DEFAULT_TIMEOUT = 25
MAX_TIMEOUT = 90

SAMPLE = {"metric": {"__name__": "up"}}
INSTANT_RESPONSE = {
    "status": "success",
    "data": {"resultType": "vector", "result": [{**SAMPLE, "value": [1, "1"]}]},
}
RANGE_RESPONSE = {
    "status": "success",
    "data": {"resultType": "matrix", "result": [{**SAMPLE, "values": [[1, "1"]]}]},
}

TOOLS = [
    pytest.param(
        ExecuteInstantQuery, "api/v1/query", INSTANT_RESPONSE, {}, id="instant"
    ),
    pytest.param(
        ExecuteRangeQuery,
        "api/v1/query_range",
        RANGE_RESPONSE,
        {"output_type": "Plain"},
        id="range",
    ),
]


def _toolset() -> PrometheusToolset:
    toolset = PrometheusToolset()
    toolset.config = PrometheusConfig(
        prometheus_url=BASE_URL,
        query_timeout_seconds_default=DEFAULT_TIMEOUT,
        query_timeout_seconds_hard_max=MAX_TIMEOUT,
    )
    return toolset


@pytest.mark.parametrize("tool_cls, path, response_json, extra_params", TOOLS)
@pytest.mark.parametrize(
    "timeout_params, expected_timeout",
    [
        pytest.param({"timeout": None}, DEFAULT_TIMEOUT, id="null"),
        pytest.param({}, DEFAULT_TIMEOUT, id="omitted"),
        pytest.param({"timeout": 40}, 40, id="explicit"),
        pytest.param({"timeout": 1000}, MAX_TIMEOUT, id="above-max"),
        pytest.param({"timeout": 0}, DEFAULT_TIMEOUT, id="below-min"),
    ],
)
def test_query_timeout(
    tool_cls, path, response_json, extra_params, timeout_params, expected_timeout
):
    tool = tool_cls(_toolset())
    params = {"query": "up", "description": "d", **extra_params, **timeout_params}

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, BASE_URL + path, json=response_json, status=200)
        result = tool.invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS, result.error
        assert len(rsps.calls) == 1
        assert rsps.calls[0].request.req_kwargs["timeout"] == expected_timeout
