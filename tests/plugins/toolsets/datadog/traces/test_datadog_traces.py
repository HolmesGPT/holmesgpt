from unittest.mock import patch, MagicMock
from holmes.plugins.toolsets.datadog.toolset_datadog_traces import (
    DatadogTracesToolset,
    FetchDatadogTracesList,
    FetchDatadogTraceById,
    FetchDatadogSpansByFilter,
)
from holmes.plugins.toolsets.datadog.datadog_api import DataDogRequestError
from holmes.core.tools import StructuredToolResultStatus
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
        assert len(toolset.tools) == 3
        assert toolset.tools[0].name == "fetch_datadog_traces"
        assert toolset.tools[1].name == "fetch_datadog_trace_by_id"
        assert toolset.tools[2].name == "fetch_datadog_spans"

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


class TestFetchDatadogTracesList:
    """Unit tests for FetchDatadogTracesList tool."""

    def setup_method(self):
        """Setup test configuration."""
        self.config = {
            "dd_api_key": "test_api_key",
            "dd_app_key": "test_app_key",
            "site_api_url": "https://api.datadoghq.com",
            "request_timeout": 60,
        }

        self.toolset = DatadogTracesToolset()
        self.toolset.dd_config = self.toolset.prerequisites_callable(self.config)
        self.tool = FetchDatadogTracesList(self.toolset)

    def test_get_parameterized_one_liner(self):
        """Test one-liner generation."""
        params = {
            "service": "web-api",
            "operation": "GET /users",
            "min_duration": "1s",
        }

        one_liner = self.tool.get_parameterized_one_liner(params)
        assert "service=web-api" in one_liner
        assert "operation=GET /users" in one_liner
        assert "duration>1s" in one_liner

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_invoke_success_with_traces(self, mock_execute):
        """Test successful invocation with traces found."""
        # Mock response with spans
        mock_execute.return_value = {
            "data": [
                {
                    "attributes": {
                        "trace_id": "abc123",
                        "span_id": "span1",
                        "service": "web-api",
                        "operation_name": "GET /users",
                        "start": 1000000000000000000,  # nanoseconds
                        "duration": 50000000,  # 50ms in nanoseconds
                    }
                },
                {
                    "attributes": {
                        "trace_id": "abc123",
                        "span_id": "span2",
                        "parent_id": "span1",
                        "service": "database",
                        "operation_name": "SELECT",
                        "start": 1000000000010000000,
                        "duration": 30000000,  # 30ms
                    }
                },
            ]
        }

        # Set up toolset config
        self.toolset.dd_config = MagicMock()
        self.toolset.dd_config.site_api_url = "https://api.datadoghq.com"
        self.toolset.dd_config.request_timeout = 60
        self.toolset.dd_config.indexes = ["*"]

        params = {
            "service": "web-api",
            "limit": 10,
        }

        result = self.tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "Found 1 traces" in result.data
        assert "traceID=abc123" in result.data
        assert "durationMs=50.00" in result.data  # 50ms total duration

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_invoke_no_traces_found(self, mock_execute):
        """Test invocation when no traces are found."""
        mock_execute.return_value = {"data": []}

        # Set up toolset config
        self.toolset.dd_config = MagicMock()
        self.toolset.dd_config.site_api_url = "https://api.datadoghq.com"
        self.toolset.dd_config.request_timeout = 60
        self.toolset.dd_config.indexes = ["*"]

        params = {"service": "non-existent-service"}

        result = self.tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.NO_DATA
        assert "No matching traces found" in result.data

    def test_invoke_no_config(self):
        """Test invocation without configuration."""
        self.toolset.dd_config = None

        result = self.tool._invoke({}, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.ERROR
        assert "Datadog configuration not initialized" in result.error

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_invoke_with_duration_filter(self, mock_execute):
        """Test invocation with duration filter."""
        mock_execute.return_value = {"data": []}

        # Set up toolset config
        self.toolset.dd_config = MagicMock()
        self.toolset.dd_config.site_api_url = "https://api.datadoghq.com"
        self.toolset.dd_config.request_timeout = 60
        self.toolset.dd_config.indexes = ["*"]

        params = {"min_duration": "500ms"}

        self.tool._invoke(params, context=create_mock_tool_invoke_context())

        # Check that the query includes duration filter
        call_args = mock_execute.call_args
        payload = call_args[1]["payload_or_params"]
        query = payload["data"]["attributes"]["filter"]["query"]
        assert "@duration:>500000000" in query

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_invoke_rate_limit_error(self, mock_execute):
        """Test handling of rate limit errors."""
        mock_execute.side_effect = DataDogRequestError(
            payload={},
            status_code=429,
            response_text="Rate limit exceeded",
            response_headers={},
        )

        # Set up toolset config
        self.toolset.dd_config = MagicMock()
        self.toolset.dd_config.site_api_url = "https://api.datadoghq.com"
        self.toolset.dd_config.request_timeout = 60
        self.toolset.dd_config.indexes = ["*"]

        result = self.tool._invoke({}, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.ERROR
        assert "rate limit exceeded" in result.error


class TestFetchDatadogTraceById:
    """Unit tests for FetchDatadogTraceById tool."""

    def setup_method(self):
        """Setup test configuration."""
        self.toolset = DatadogTracesToolset()
        self.toolset.dd_config = MagicMock()
        self.toolset.dd_config.site_api_url = "https://api.datadoghq.com"
        self.toolset.dd_config.request_timeout = 60
        self.tool = FetchDatadogTraceById(self.toolset)

    def test_get_parameterized_one_liner(self):
        """Test one-liner generation."""
        params = {"trace_id": "abc123"}
        one_liner = self.tool.get_parameterized_one_liner(params)
        assert "Datadog: Fetch Trace Details (abc123)" == one_liner

    def test_invoke_missing_trace_id(self):
        """Test invocation without trace_id parameter."""
        result = self.tool._invoke({}, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.ERROR
        assert "trace_id parameter is required" in result.error

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_invoke_success_with_spans(self, mock_execute):
        """Test successful invocation with trace spans."""
        # Mock response with hierarchical spans
        mock_execute.return_value = {
            "data": [
                {
                    "attributes": {
                        "trace_id": "abc123",
                        "span_id": "span1",
                        "service": "web-api",
                        "operation_name": "GET /users",
                        "resource_name": "/api/v1/users",
                        "start": 1000000000000000000,
                        "duration": 50000000,
                        "status": "ok",
                        "tags": ["env:production", "version:1.2.3"],
                    }
                },
                {
                    "attributes": {
                        "trace_id": "abc123",
                        "span_id": "span2",
                        "parent_id": "span1",
                        "service": "database",
                        "operation_name": "SELECT",
                        "resource_name": "SELECT * FROM users",
                        "start": 1000000010000000000,
                        "duration": 30000000,
                        "status": "ok",
                        "tags": ["db.type:postgres"],
                    }
                },
            ]
        }

        params = {"trace_id": "abc123"}

        result = self.tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "Trace ID: abc123" in result.data
        assert "GET /users (web-api)" in result.data
        assert "SELECT (database)" in result.data
        assert "50.00ms" in result.data
        assert "30.00ms" in result.data

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_invoke_no_trace_found(self, mock_execute):
        """Test invocation when trace is not found."""
        mock_execute.return_value = {"data": []}

        params = {"trace_id": "nonexistent"}

        result = self.tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.NO_DATA
        assert "No trace found for trace_id: nonexistent" in result.data


class TestFetchDatadogSpansByFilter:
    """Unit tests for FetchDatadogSpansByFilter tool."""

    def setup_method(self):
        """Setup test configuration."""
        self.toolset = DatadogTracesToolset()
        self.toolset.dd_config = MagicMock()
        self.toolset.dd_config.site_api_url = "https://api.datadoghq.com"
        self.toolset.dd_config.request_timeout = 60
        self.tool = FetchDatadogSpansByFilter(self.toolset)

    def test_get_parameterized_one_liner(self):
        """Test one-liner generation."""
        # Test with query
        params = {"query": "@http.status_code:500"}
        one_liner = self.tool.get_parameterized_one_liner(params)
        assert "Datadog: Search Spans (@http.status_code:500)" == one_liner

        # Test with filters
        params = {"service": "web-api", "operation": "GET /users"}
        one_liner = self.tool.get_parameterized_one_liner(params)
        assert "service=web-api" in one_liner
        assert "operation=GET /users" in one_liner

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
        assert "Found 1 matching spans" in result.data
        assert "Trace ID: trace1" in result.data
        assert "GET /users (web-api)" in result.data
        assert "status: error" in result.data

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_invoke_with_tags_filter(self, mock_execute):
        """Test invocation with tags filter."""
        mock_execute.return_value = {"data": []}

        params = {
            "service": "web-api",
            "tags": {"env": "production", "version": "1.2.3"},
        }

        self.tool._invoke(params, context=create_mock_tool_invoke_context())

        # Check that tags are included in the query
        call_args = mock_execute.call_args
        payload = call_args[1]["payload_or_params"]
        query = payload["data"]["attributes"]["filter"]["query"]
        assert "@env:production" in query
        assert "@version:1.2.3" in query

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
    )
    def test_invoke_no_spans_found(self, mock_execute):
        """Test invocation when no spans are found."""
        mock_execute.return_value = {"data": []}

        params = {"service": "non-existent"}

        result = self.tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.NO_DATA
        assert "No matching spans found" in result.data
