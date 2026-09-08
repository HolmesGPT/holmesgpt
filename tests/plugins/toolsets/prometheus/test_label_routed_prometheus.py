"""Unit tests for the label-routed Prometheus toolset.

Every tool must build its request URL as:
    {prometheus_url}/prometheus/{label_value}/api/v1/...

and must refuse to run (with a clear, actionable error) when `label_value`
is missing. HTTP is mocked with `responses` per repo convention.
"""

import responses

from holmes.core.tools import StructuredToolResultStatus, ToolsetStatusEnum
from holmes.plugins.toolsets.prometheus.label_routed_prometheus import (
    LabelRoutedPrometheusConfig,
    LabelRoutedPrometheusToolset,
)
from tests.conftest import create_mock_tool_invoke_context

BASE_URL = "http://prometheus-gateway.monitoring.svc:80"


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

    def test_default_label_key(self):
        ts = _toolset()
        assert ts.config.label_key == "productline"

    def test_custom_label_key(self):
        ts = _toolset(label_key="tenant")
        assert ts.config.label_key == "tenant"

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
    """Every tool must refuse to run without label_value, with a clear error
    naming the configured label_key so the LLM knows what to fetch first."""

    def _assert_missing_label_error(self, ts, tool_name, params):
        tool = _tool(ts, tool_name)
        result = tool.invoke(params, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert "label_value" in result.error
        assert "productline" in result.error

    def test_range_query_missing_label_value(self):
        ts = _toolset()
        self._assert_missing_label_error(
            ts,
            "label_routed_execute_prometheus_range_query",
            {"query": "up", "description": "d", "output_type": "Plain"},
        )

    def test_instant_query_missing_label_value(self):
        ts = _toolset()
        self._assert_missing_label_error(
            ts,
            "label_routed_execute_prometheus_instant_query",
            {"query": "up", "description": "d"},
        )

    def test_list_rules_missing_label_value(self):
        ts = _toolset()
        self._assert_missing_label_error(ts, "label_routed_list_prometheus_rules", {})

    def test_get_series_missing_label_value(self):
        ts = _toolset()
        self._assert_missing_label_error(ts, "label_routed_get_series", {"match": "up"})

    def test_get_label_values_missing_label_value(self):
        ts = _toolset()
        self._assert_missing_label_error(
            ts, "label_routed_get_label_values", {"label": "pod"}
        )

    def test_get_all_labels_missing_label_value(self):
        ts = _toolset()
        self._assert_missing_label_error(ts, "label_routed_get_all_labels", {})

    def test_get_metric_names_missing_label_value(self):
        ts = _toolset()
        self._assert_missing_label_error(
            ts, "label_routed_get_metric_names", {"match": "up"}
        )

    def test_get_metric_metadata_missing_label_value(self):
        ts = _toolset()
        self._assert_missing_label_error(ts, "label_routed_get_metric_metadata", {})

    def test_uses_configured_label_key_in_error(self):
        ts = _toolset(label_key="tenant")
        tool = _tool(ts, "label_routed_execute_prometheus_instant_query")
        result = tool.invoke(
            {"query": "up", "description": "d"}, create_mock_tool_invoke_context()
        )
        assert "tenant" in result.error
        assert "productline" not in result.error


class TestUrlRouting:
    """URL must be {prometheus_url}/prometheus/{label_value}/api/v1/... and
    the label value must be percent-encoded as a path segment."""

    def test_range_query_url(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{BASE_URL}/prometheus/acme/api/v1/query_range",
                json={
                    "status": "success",
                    "data": {"result": [{"values": [[1, "1"]]}]},
                },
                status=200,
            )
            tool = _tool(ts, "label_routed_execute_prometheus_range_query")
            result = tool.invoke(
                {
                    "label_value": "acme",
                    "query": "up",
                    "description": "d",
                    "output_type": "Plain",
                },
                create_mock_tool_invoke_context(),
            )
            called_url = rsps.calls[0].request.url
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert called_url.startswith(f"{BASE_URL}/prometheus/acme/api/v1/query_range")

    def test_instant_query_url(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{BASE_URL}/prometheus/acme/api/v1/query",
                json={"status": "success", "data": {"result": [{"value": [1, "1"]}]}},
                status=200,
            )
            tool = _tool(ts, "label_routed_execute_prometheus_instant_query")
            result = tool.invoke(
                {"label_value": "acme", "query": "up", "description": "d"},
                create_mock_tool_invoke_context(),
            )
            called_url = rsps.calls[0].request.url
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert called_url.startswith(f"{BASE_URL}/prometheus/acme/api/v1/query")

    def test_list_rules_url(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/prometheus/acme/api/v1/rules",
                json={"status": "success", "data": {"groups": []}},
                status=200,
            )
            tool = _tool(ts, "label_routed_list_prometheus_rules")
            result = tool.invoke(
                {"label_value": "acme"}, create_mock_tool_invoke_context()
            )
            called_url = rsps.calls[0].request.url
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert called_url.startswith(f"{BASE_URL}/prometheus/acme/api/v1/rules")

    def test_get_series_url(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/prometheus/acme/api/v1/series",
                json={"status": "success", "data": []},
                status=200,
            )
            tool = _tool(ts, "label_routed_get_series")
            result = tool.invoke(
                {"label_value": "acme", "match": "up"},
                create_mock_tool_invoke_context(),
            )
            called_url = rsps.calls[0].request.url
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert called_url.startswith(f"{BASE_URL}/prometheus/acme/api/v1/series")

    def test_get_label_values_url(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/prometheus/acme/api/v1/label/pod/values",
                json={"status": "success", "data": ["pod-1"]},
                status=200,
            )
            tool = _tool(ts, "label_routed_get_label_values")
            result = tool.invoke(
                {"label_value": "acme", "label": "pod"},
                create_mock_tool_invoke_context(),
            )
            called_url = rsps.calls[0].request.url
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert called_url.startswith(
            f"{BASE_URL}/prometheus/acme/api/v1/label/pod/values"
        )

    def test_get_all_labels_url(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/prometheus/acme/api/v1/labels",
                json={"status": "success", "data": ["pod", "namespace"]},
                status=200,
            )
            tool = _tool(ts, "label_routed_get_all_labels")
            result = tool.invoke(
                {"label_value": "acme"}, create_mock_tool_invoke_context()
            )
            called_url = rsps.calls[0].request.url
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert called_url.startswith(f"{BASE_URL}/prometheus/acme/api/v1/labels")

    def test_get_metric_names_url(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/prometheus/acme/api/v1/label/__name__/values",
                json={"status": "success", "data": ["up"]},
                status=200,
            )
            tool = _tool(ts, "label_routed_get_metric_names")
            result = tool.invoke(
                {"label_value": "acme", "match": "up"},
                create_mock_tool_invoke_context(),
            )
            called_url = rsps.calls[0].request.url
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert called_url.startswith(
            f"{BASE_URL}/prometheus/acme/api/v1/label/__name__/values"
        )

    def test_get_metric_metadata_url(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/prometheus/acme/api/v1/metadata",
                json={"status": "success", "data": {}},
                status=200,
            )
            tool = _tool(ts, "label_routed_get_metric_metadata")
            result = tool.invoke(
                {"label_value": "acme"}, create_mock_tool_invoke_context()
            )
            called_url = rsps.calls[0].request.url
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert called_url.startswith(f"{BASE_URL}/prometheus/acme/api/v1/metadata")

    def test_different_label_values_route_to_different_paths(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/prometheus/client-a/api/v1/labels",
                json={"status": "success", "data": []},
                status=200,
            )
            rsps.add(
                responses.GET,
                f"{BASE_URL}/prometheus/client-b/api/v1/labels",
                json={"status": "success", "data": []},
                status=200,
            )
            tool = _tool(ts, "label_routed_get_all_labels")
            tool.invoke({"label_value": "client-a"}, create_mock_tool_invoke_context())
            tool.invoke({"label_value": "client-b"}, create_mock_tool_invoke_context())
            url_a = rsps.calls[0].request.url
            url_b = rsps.calls[1].request.url
        assert url_a.startswith(f"{BASE_URL}/prometheus/client-a/")
        assert url_b.startswith(f"{BASE_URL}/prometheus/client-b/")

    def test_label_value_is_percent_encoded_as_path_segment(self):
        """A label value containing '/' must not be able to inject an extra
        path segment or escape the per-client path prefix."""
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "http://prometheus-gateway.monitoring.svc:80/prometheus/weird%2Fvalue/api/v1/labels",
                json={"status": "success", "data": []},
                status=200,
            )
            tool = _tool(ts, "label_routed_get_all_labels")
            result = tool.invoke(
                {"label_value": "weird/value"}, create_mock_tool_invoke_context()
            )
            called_url = rsps.calls[0].request.url
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "/prometheus/weird%2Fvalue/api/v1/labels" in called_url
        assert "/prometheus/weird/value/" not in called_url


class TestPassesHeaders:
    def test_additional_headers_sent(self):
        ts = _toolset(additional_headers={"Authorization": "Bearer secret"})
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{BASE_URL}/prometheus/acme/api/v1/labels",
                json={"status": "success", "data": []},
                status=200,
            )
            tool = _tool(ts, "label_routed_get_all_labels")
            tool.invoke({"label_value": "acme"}, create_mock_tool_invoke_context())
            auth_header = rsps.calls[0].request.headers.get("Authorization")
        assert auth_header == "Bearer secret"


class TestNoResultReportedAsFailed:
    def test_range_query_empty_result_is_failed_status(self):
        ts = _toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{BASE_URL}/prometheus/acme/api/v1/query_range",
                json={"status": "success", "data": {"result": []}},
                status=200,
            )
            tool = _tool(ts, "label_routed_execute_prometheus_range_query")
            result = tool.invoke(
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
