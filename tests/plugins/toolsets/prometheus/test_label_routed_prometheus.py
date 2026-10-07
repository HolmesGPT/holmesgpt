"""Unit tests for the label-routed Prometheus toolset.

Every tool must send its request to the static `{prometheus_url}api/v1/...`
with the configured routing header (default `X-Scope-OrgID`) set to
`label_value`, and must refuse to run (with a clear, actionable error) when
`label_value` is missing. HTTP is mocked with `responses` per repo convention.
"""

import pytest
import responses

from holmes.core.tools import StructuredToolResultStatus, ToolsetStatusEnum
from holmes.plugins.toolsets.prometheus.label_routed_prometheus import (
    LabelRoutedPrometheusConfig,
    LabelRoutedPrometheusToolset,
)
from tests.conftest import create_mock_tool_invoke_context

BASE_URL = "http://mimir.monitoring.svc/prometheus"

# (tool name, HTTP method, API path, extra params, mocked response JSON)
TOOL_CASES = [
    (
        "label_routed_execute_prometheus_range_query",
        responses.POST,
        "api/v1/query_range",
        {"query": "up", "description": "d", "output_type": "Plain"},
        {"status": "success", "data": {"result": [{"values": [[1, "1"]]}]}},
    ),
    (
        "label_routed_execute_prometheus_instant_query",
        responses.POST,
        "api/v1/query",
        {"query": "up", "description": "d"},
        {"status": "success", "data": {"result": [{"value": [1, "1"]}]}},
    ),
    (
        "label_routed_list_prometheus_rules",
        responses.GET,
        "api/v1/rules",
        {},
        {"status": "success", "data": {"groups": []}},
    ),
    (
        "label_routed_get_series",
        responses.GET,
        "api/v1/series",
        {"match": "up"},
        {"status": "success", "data": []},
    ),
    (
        "label_routed_get_label_values",
        responses.GET,
        "api/v1/label/pod/values",
        {"label": "pod"},
        {"status": "success", "data": ["pod-1"]},
    ),
    (
        "label_routed_get_all_labels",
        responses.GET,
        "api/v1/labels",
        {},
        {"status": "success", "data": ["pod", "namespace"]},
    ),
    (
        "label_routed_get_metric_names",
        responses.GET,
        "api/v1/label/__name__/values",
        {"match": "up"},
        {"status": "success", "data": ["up"]},
    ),
    (
        "label_routed_get_metric_metadata",
        responses.GET,
        "api/v1/metadata",
        {},
        {"status": "success", "data": {}},
    ),
]
TOOL_IDS = [case[0] for case in TOOL_CASES]


def _toolset(**config_overrides) -> LabelRoutedPrometheusToolset:
    ts = LabelRoutedPrometheusToolset()
    config = {"prometheus_url": BASE_URL, **config_overrides}
    ok, msg = ts.prerequisites_callable(config)
    assert ok, msg
    return ts


def _tool(ts: LabelRoutedPrometheusToolset, name: str):
    return next(t for t in ts.tools if t.name == name)


class TestConfig:
    def test_requires_prometheus_url(self):
        ts = LabelRoutedPrometheusToolset()
        ok, msg = ts.prerequisites_callable({})
        assert ok is False
        assert "Invalid label-routed Prometheus configuration" in msg

    def test_defaults(self):
        ts = _toolset()
        assert ts.config.label_key == "productline"
        assert ts.config.routing_header == "X-Scope-OrgID"

    def test_custom_label_key_and_header(self):
        ts = _toolset(label_key="tenant", routing_header="X-Tenant")
        assert ts.config.label_key == "tenant"
        assert ts.config.routing_header == "X-Tenant"

    def test_trailing_slash_normalized(self):
        config = LabelRoutedPrometheusConfig(prometheus_url="http://host:80")
        assert config.prometheus_url == "http://host:80/"

    def test_check_prerequisites_enables_toolset(self):
        ts = LabelRoutedPrometheusToolset()
        ts.config = {"prometheus_url": BASE_URL}
        ts.check_prerequisites()
        assert ts.status == ToolsetStatusEnum.ENABLED
        assert ts.error is None


class TestMissingLabelValue:
    """Every tool must refuse to run without label_value, before any HTTP
    call, with a clear error naming the configured label_key so the LLM knows
    what to fetch first."""

    @pytest.mark.parametrize("tool_name,_m,_p,params,_r", TOOL_CASES, ids=TOOL_IDS)
    def test_missing_label_value(self, tool_name, _m, _p, params, _r):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            result = _tool(ts, tool_name).invoke(
                params, create_mock_tool_invoke_context()
            )
            assert len(rsps.calls) == 0
        assert result.status == StructuredToolResultStatus.ERROR
        assert "label_value" in result.error
        assert "productline" in result.error

    def test_empty_label_value(self):
        ts = _toolset()
        result = _tool(ts, "label_routed_get_all_labels").invoke(
            {"label_value": ""}, create_mock_tool_invoke_context()
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "label_value" in result.error

    def test_uses_configured_label_key_in_error(self):
        ts = _toolset(label_key="tenant")
        result = _tool(ts, "label_routed_execute_prometheus_instant_query").invoke(
            {"query": "up", "description": "d"}, create_mock_tool_invoke_context()
        )
        assert "tenant" in result.error
        assert "productline" not in result.error


class TestHeaderRouting:
    """The URL stays `{prometheus_url}api/v1/...` for every tenant; the
    tenant travels in the routing header."""

    @pytest.mark.parametrize(
        "tool_name,method,path,params,response_json", TOOL_CASES, ids=TOOL_IDS
    )
    def test_static_url_and_routing_header(
        self, tool_name, method, path, params, response_json
    ):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(method, f"{BASE_URL}/{path}", json=response_json, status=200)
            result = _tool(ts, tool_name).invoke(
                {"label_value": "corporate", **params},
                create_mock_tool_invoke_context(),
            )
            request = rsps.calls[0].request
        assert result.status == StructuredToolResultStatus.SUCCESS, result.error
        assert request.url.startswith(f"{BASE_URL}/{path}")
        assert request.headers["X-Scope-OrgID"] == "corporate"

    def test_different_label_values_send_different_headers(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/api/v1/labels",
                json={"status": "success", "data": []},
                status=200,
            )
            rsps.add(
                responses.GET,
                f"{BASE_URL}/api/v1/labels",
                json={"status": "success", "data": []},
                status=200,
            )
            tool = _tool(ts, "label_routed_get_all_labels")
            tool.invoke({"label_value": "client-a"}, create_mock_tool_invoke_context())
            tool.invoke({"label_value": "client-b"}, create_mock_tool_invoke_context())
            header_a = rsps.calls[0].request.headers["X-Scope-OrgID"]
            header_b = rsps.calls[1].request.headers["X-Scope-OrgID"]
        assert header_a == "client-a"
        assert header_b == "client-b"
        # The config's headers must not be mutated by per-call routing
        assert "X-Scope-OrgID" not in ts.config.additional_headers

    def test_custom_routing_header(self):
        ts = _toolset(routing_header="X-Tenant")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/api/v1/labels",
                json={"status": "success", "data": []},
                status=200,
            )
            _tool(ts, "label_routed_get_all_labels").invoke(
                {"label_value": "acme"}, create_mock_tool_invoke_context()
            )
            headers = rsps.calls[0].request.headers
        assert headers["X-Tenant"] == "acme"
        assert "X-Scope-OrgID" not in headers

    def test_additional_headers_sent_alongside_routing_header(self):
        ts = _toolset(additional_headers={"Authorization": "Bearer secret"})
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/api/v1/labels",
                json={"status": "success", "data": []},
                status=200,
            )
            _tool(ts, "label_routed_get_all_labels").invoke(
                {"label_value": "acme"}, create_mock_tool_invoke_context()
            )
            headers = rsps.calls[0].request.headers
        assert headers["Authorization"] == "Bearer secret"
        assert headers["X-Scope-OrgID"] == "acme"

    def test_routing_header_overrides_static_header_of_same_name(self):
        ts = _toolset(additional_headers={"X-Scope-OrgID": "static-tenant"})
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/api/v1/labels",
                json={"status": "success", "data": []},
                status=200,
            )
            _tool(ts, "label_routed_get_all_labels").invoke(
                {"label_value": "acme"}, create_mock_tool_invoke_context()
            )
            headers = rsps.calls[0].request.headers
        assert headers["X-Scope-OrgID"] == "acme"

    def test_label_value_with_newline_is_rejected(self):
        """A label value must not be able to inject an extra header."""
        ts = _toolset()
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            result = _tool(ts, "label_routed_get_all_labels").invoke(
                {"label_value": "acme\r\nX-Injected: 1"},
                create_mock_tool_invoke_context(),
            )
            assert len(rsps.calls) == 0
        assert result.status == StructuredToolResultStatus.ERROR


class TestInstructions:
    def test_additional_labels_rendered_in_instructions(self):
        ts = _toolset(additional_labels={"k8s_cluster_name": "gb03prod2"})
        assert 'k8s_cluster_name="gb03prod2"' in ts.llm_instructions
        assert "label_routed_get_metric_names" in ts.llm_instructions

    def test_no_additional_labels_block_without_config(self):
        ts = _toolset()
        assert "ALWAYS add the following label matchers" not in ts.llm_instructions


class TestNoResultReportedAsFailed:
    def test_range_query_empty_result_is_failed_status(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{BASE_URL}/api/v1/query_range",
                json={"status": "success", "data": {"result": []}},
                status=200,
            )
            result = _tool(ts, "label_routed_execute_prometheus_range_query").invoke(
                {
                    "label_value": "acme",
                    "query": "up",
                    "description": "d",
                    "output_type": "Plain",
                },
                create_mock_tool_invoke_context(),
            )
        assert result.data.status == "Failed"
        assert "no result" in result.data.error_message
