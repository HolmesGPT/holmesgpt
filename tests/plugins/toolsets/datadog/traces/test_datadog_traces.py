from unittest.mock import MagicMock, patch

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.datadog.datadog_api import DataDogRequestError
from holmes.plugins.toolsets.datadog.toolset_datadog_traces import (
    DatadogTracesToolset,
    GetSpans,
)
from tests.conftest import create_mock_tool_invoke_context


class TestDatadogTracesToolset:
    """Unit tests for Datadog traces toolset."""

    def setup_method(self):
        """Setup test configuration."""
        self.config = {
            "dd_api_key": "test_api_key",
            "dd_app_key": "test_app_key",
            "site_api_url": "https://api.datadoghq.com",
            "request_timeout": 60,
        }

    def test_toolset_initialization(self):
        """Test toolset initialization."""
        toolset = DatadogTracesToolset()
        assert toolset.name == "datadog/traces"
        assert len(toolset.tools) == 2
        assert toolset.tools[0].name == "fetch_datadog_spans"
        assert toolset.tools[1].name == "aggregate_datadog_spans"

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_prerequisites_success(self, mock_execute):
        """Test successful prerequisites check."""
        mock_execute.return_value = {"data": []}

        toolset = DatadogTracesToolset()
        success, error_msg = toolset.prerequisites_callable(self.config)

        assert success
        assert error_msg == ""

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_prerequisites_permission_error(self, mock_execute):
        """Test prerequisites check with permission error."""
        mock_execute.side_effect = DataDogRequestError(
            payload={},
            status_code=403,
            response_text="Forbidden",
            response_headers={},
        )

        toolset = DatadogTracesToolset()
        success, error_msg = toolset.prerequisites_callable(self.config)

        assert not success
        assert "API key lacks required permissions" in error_msg

    def test_prerequisites_no_config(self):
        """Test prerequisites check with no configuration."""
        toolset = DatadogTracesToolset()
        success, error_msg = toolset.prerequisites_callable({})

        assert not success
        assert "No configuration provided" in error_msg

    def test_get_example_config(self):
        """Test get_example_config method."""
        toolset = DatadogTracesToolset()
        example_config = toolset.get_example_config()

        assert "dd_api_key" in example_config
        assert "dd_app_key" in example_config
        assert "site_api_url" in example_config
        assert "request_timeout" in example_config


class TestFetchDatadogSpansByFilter:
    """Unit tests for FetchDatadogSpansByFilter tool."""

    def setup_method(self):
        """Setup test configuration."""
        self.toolset = DatadogTracesToolset()
        self.toolset.dd_config = MagicMock()
        self.toolset.dd_config.site_api_url = "https://api.datadoghq.com"
        self.toolset.dd_config.request_timeout = 60
        self.tool = GetSpans(self.toolset)

    def test_get_parameterized_one_liner(self):
        """Test one-liner generation."""
        # Test with query
        params = {"query": "@http.status_code:500"}
        one_liner = self.tool.get_parameterized_one_liner(params)
        assert "Datadog: Search Spans (@http.status_code:500)" == one_liner

        # Test without query
        params = {}
        one_liner = self.tool.get_parameterized_one_liner(params)
        assert "Datadog: Search Spans ()" == one_liner

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_invoke_with_custom_query(self, mock_execute):
        """Test invocation with custom Datadog query."""
        mock_execute.return_value = {
            "data": [
                {
                    "attributes": {
                        "trace_id": "trace1",
                        "span_id": "span1",
                        "service": "web-api",
                        "operation_name": "GET /users",
                        "start": 1000000000000000000,
                        "duration": 100000000,
                        "status": "error",
                        "tags": ["http.status_code:500", "error.type:ServerError"],
                    }
                }
            ]
        }

        params = {"query": "@http.status_code:500"}

        result = self.tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        # The tool returns raw response data, not formatted text
        assert result.data == mock_execute.return_value
        assert len(result.data["data"]) == 1
        assert result.data["data"][0]["attributes"]["trace_id"] == "trace1"

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_invoke_with_tags_filter(self, mock_execute):
        """Test invocation with tags in query."""
        mock_execute.return_value = {"data": []}

        params = {
            "query": "service:web-api @env:production @version:1.2.3",
        }

        self.tool._invoke(params, context=create_mock_tool_invoke_context())

        # Check that the query was passed correctly
        call_args = mock_execute.call_args
        payload = call_args[1]["payload_or_params"]
        query = payload["data"]["attributes"]["filter"]["query"]
        assert query == "service:web-api @env:production @version:1.2.3"

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_invoke_no_spans_found(self, mock_execute):
        """Test invocation when no spans are found."""
        mock_execute.return_value = {"data": []}

        params = {"query": "service:non-existent"}

        result = self.tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        # When no data is found, the tool still returns success with empty data
        assert result.data == mock_execute.return_value
        assert len(result.data["data"]) == 0
