from unittest.mock import Mock, patch

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.datadog.toolset_datadog_metrics import (
    DatadogMetricsConfig,
    DatadogMetricsToolset,
)
from tests.conftest import create_mock_tool_invoke_context


class TestDatadogMetricsToolset:
    def setup_method(self):
        self.config = DatadogMetricsConfig(
            dd_api_key="test-api-key",
            dd_app_key="test-app-key",
            site_api_url="https://api.datadoghq.com",
            default_limit=1000,
            request_timeout=60,
        )

        self.toolset = DatadogMetricsToolset()
        self.toolset.dd_config = self.config

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    def test_list_active_metrics(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "metrics": [
                "system.cpu.user",
                "system.cpu.system",
                "system.mem.used",
                "system.disk.read",
                "system.disk.write",
            ]
        }
        mock_get.return_value = response

        params = {}
        tool = self.toolset.tools[0]
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "system.cpu.user" in result.data
        assert "system.mem.used" in result.data
        assert "Metric Name" in result.data

        call_args = mock_get.call_args
        assert "/api/v1/metrics" in call_args[0][0]
        assert call_args[1]["params"]["from"]
        # Should have a default timestamp

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    def test_list_active_metrics_with_filters(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "metrics": [
                "system.cpu.user",
                "system.cpu.system",
            ]
        }
        mock_get.return_value = response

        params = {
            "host": "test-host",
            "tag_filter": "env:production",
            "from_time": "2023-01-01T00:00:00Z",
        }
        tool = self.toolset.tools[0]
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS

        call_args = mock_get.call_args
        assert call_args[1]["params"]["host"] == "test-host"
        assert call_args[1]["params"]["tag_filter"] == "env:production"
        assert call_args[1]["params"]["from"]
        # from_time should be converted to unix timestamp

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    def test_query_metrics(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "series": [
                {
                    "metric": "system.cpu.user",
                    "points": [
                        [1609459200, 10.5],
                        [1609459260, 12.3],
                        [1609459320, 11.7],
                    ],
                    "tags": ["host:test-host"],
                }
            ]
        }
        mock_get.return_value = response

        params = {
            "query": "system.cpu.user{host:test-host}",
            "from_time": "2021-01-01T00:00:00Z",
            "to_time": "2021-01-01T01:00:00Z",
        }
        tool = self.toolset.tools[1]
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        # Check that the metric name appears in the structured data
        assert result.data["query"] == "system.cpu.user{host:test-host}"
        assert (
            result.data["data"]["result"][0]["metric"]["__name__"] == "system.cpu.user"
        )

        call_args = mock_get.call_args
        assert "/api/v1/query" in call_args[0][0]
        assert call_args[1]["params"]["query"] == "system.cpu.user{host:test-host}"

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    def test_query_metrics_no_data(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"series": []}
        mock_get.return_value = response

        params = {
            "query": "nonexistent.metric{*}",
        }
        tool = self.toolset.tools[1]
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.NO_DATA
        assert "no data" in result.error.lower()

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    def test_get_metric_metadata(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "description": "The amount of CPU time in user space",
            "short_name": "cpu user",
            "integration": "system",
            "per_unit": "second",
            "type": "gauge",
            "unit": "percent",
        }
        mock_get.return_value = response

        params = {"metric_names": "system.cpu.user"}
        tool = self.toolset.tools[2]
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        data = result.data  # Data is now a dict, not a JSON string
        assert "metrics_metadata" in data
        assert "system.cpu.user" in data["metrics_metadata"]
        assert data["successful"] == 1
        assert data["failed"] == 0

        call_args = mock_get.call_args
        assert "/api/v1/metrics/system.cpu.user" in call_args[0][0]

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    def test_get_metric_metadata_not_found(self, mock_get):
        response = Mock()
        response.status_code = 404
        response.text = "Metric not found"
        mock_get.return_value = response

        params = {"metric_names": "nonexistent.metric"}
        tool = self.toolset.tools[2]
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.ERROR
        data = result.data  # Data is now a dict, not a JSON string
        assert "errors" in data
        assert "nonexistent.metric" in data["errors"]
        assert data["failed"] == 1

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    def test_get_multiple_metrics_metadata(self, mock_get):
        responses = [
            {
                "description": "The amount of CPU time in user space",
                "type": "gauge",
                "unit": "percent",
            },
            {
                "description": "The amount of physical RAM in use",
                "type": "gauge",
                "unit": "byte",
            },
        ]

        mock_get.side_effect = [
            Mock(status_code=200, json=Mock(return_value=responses[0])),
            Mock(status_code=200, json=Mock(return_value=responses[1])),
        ]

        params = {"metric_names": "system.cpu.user, system.mem.used"}
        tool = self.toolset.tools[2]
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        data = result.data  # Data is now a dict, not a JSON string
        assert "metrics_metadata" in data
        assert "system.cpu.user" in data["metrics_metadata"]
        assert "system.mem.used" in data["metrics_metadata"]
        assert data["successful"] == 2
        assert data["failed"] == 0
        assert data["total_requested"] == 2

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    def test_get_multiple_metrics_metadata_partial_failure(self, mock_get):
        mock_get.side_effect = [
            Mock(status_code=200, json=Mock(return_value={"type": "gauge"})),
            Mock(status_code=404, text="Metric not found"),
        ]

        params = {"metric_names": "system.cpu.user, nonexistent.metric"}
        tool = self.toolset.tools[2]
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        data = result.data  # Data is now a dict, not a JSON string
        assert "system.cpu.user" in data["metrics_metadata"]
        assert "nonexistent.metric" in data["errors"]
        assert data["successful"] == 1
        assert data["failed"] == 1

    def test_no_config(self):
        self.toolset.dd_config = None

        params = {}
        tool = self.toolset.tools[0]
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.ERROR
        assert result.error == "The toolset is missing its configuration"

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    @patch("time.sleep", return_value=None)  # Mock time.sleep to avoid delays
    def test_rate_limiting(self, mock_sleep, mock_get):
        response = Mock()
        response.status_code = 429
        response.headers = {}
        response.text = "Rate limit exceeded"
        mock_get.return_value = response

        params = {}
        tool = self.toolset.tools[0]
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.ERROR
        assert "rate limit exceeded" in result.error.lower()
        assert "5 retry attempts" in result.error

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    def test_healthcheck_success(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"valid": True}
        mock_get.return_value = response

        success, error_msg = self.toolset._perform_healthcheck(self.config)

        assert success is True
        assert error_msg == ""

        call_args = mock_get.call_args
        assert "/api/v1/validate" in call_args[0][0]

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    def test_healthcheck_failure(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"valid": False}
        mock_get.return_value = response

        success, error_msg = self.toolset._perform_healthcheck(self.config)

        assert success is False
        assert "validation failed" in error_msg.lower()

    @patch("holmes.plugins.toolsets.datadog.datadog_api.requests.get")
    def test_prerequisites_callable_success(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"valid": True}
        mock_get.return_value = response

        config = {
            "dd_api_key": "test-api-key",
            "dd_app_key": "test-app-key",
            "site_api_url": "https://api.datadoghq.com",
        }

        success, error_msg = self.toolset.prerequisites_callable(config)

        assert success is True
        assert error_msg == ""
        assert self.toolset.dd_config is not None

    def test_prerequisites_callable_missing_config(self):
        success, error_msg = self.toolset.prerequisites_callable(None)

        assert success is False
        assert (
            error_msg
            == "Missing config for dd_api_key, dd_app_key, or site_api_url. For details: https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/"
        )

    def test_prerequisites_callable_invalid_config(self):
        config = {
            "dd_api_key": "test-api-key",
        }

        success, error_msg = self.toolset.prerequisites_callable(config)

        assert success is False
        assert "Failed to parse Datadog configuration" in error_msg

    def test_get_example_config(self):
        example = self.toolset.get_example_config()

        assert "dd_api_key" in example
        assert "dd_app_key" in example
        assert "site_api_url" in example
        assert example["site_api_url"] == "https://api.datadoghq.com/"
