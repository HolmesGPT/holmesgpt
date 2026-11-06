"""Strict tool calling sends `null` for optional params the model leaves unset."""

import pytest
import responses

from holmes.core.openai_formatting import format_tool_to_open_ai_standard
from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.prometheus.prometheus import PrometheusToolset
from tests.conftest import create_mock_tool_invoke_context

PROM_URL = "http://prometheus.test:9090/"


@pytest.fixture
def toolset() -> PrometheusToolset:
    ts = PrometheusToolset()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, PROM_URL + "api/v1/query", json={"status": "success", "data": {"result": []}})
        ok, msg = ts.prerequisites_callable({"prometheus_url": PROM_URL})
    assert ok, msg
    return ts


def _tool(ts: PrometheusToolset, name: str):
    return next(t for t in ts.tools if t.name == name)


def test_strict_schema_asks_for_null_timeout(toolset):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("holmes.core.openai_formatting.STRICT_TOOL_CALLS_ENABLED", True)
        tool = _tool(toolset, "execute_prometheus_instant_query")
        schema = format_tool_to_open_ai_standard(tool.name, tool.description, tool.parameters)
    params = schema["function"]["parameters"]
    assert "timeout" in params["required"]
    assert {"type": "null"} in params["properties"]["timeout"]["anyOf"]


@pytest.mark.parametrize(
    "tool_name,path,params,body",
    [
        (
            "execute_prometheus_instant_query",
            "api/v1/query",
            {"query": "up", "description": "d", "timeout": None},
            {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "1"]}]}},
        ),
        (
            "execute_prometheus_range_query",
            "api/v1/query_range",
            {
                "query": "up",
                "description": "d",
                "timeout": None,
                "start": None,
                "end": None,
                "step": None,
                "output_type": None,
            },
            {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {}, "values": [[1, "1"]]}]}},
        ),
    ],
)
def test_null_optional_params_use_defaults(toolset, tool_name, path, params, body):
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, PROM_URL + path, json=body)
        result = _tool(toolset, tool_name).invoke(params, create_mock_tool_invoke_context())
        assert len(rsps.calls) == 1
        assert rsps.calls[0].request.req_kwargs["timeout"] == toolset.config.query_timeout_seconds_default

    assert result.status == StructuredToolResultStatus.SUCCESS, result.error
    assert result.data.status == "success"
