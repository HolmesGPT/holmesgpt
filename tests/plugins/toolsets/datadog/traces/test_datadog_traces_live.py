import os

import pytest

from holmes.plugins.toolsets.datadog.toolset_datadog_traces import (
    DatadogTracesToolset,
)
from tests.conftest import create_mock_tool_invoke_context


@pytest.mark.skipif(
    not all([os.getenv("DD_API_KEY"), os.getenv("DD_APP_KEY")]),
    reason="Datadog API credentials not available",
)
class TestDatadogTracesLiveIntegration:
    """
    Live integration tests for Datadog traces toolset.
    These tests require valid Datadog API credentials set as environment variables.
    """

    def setup_method(self):
        """Setup the toolset with real Datadog credentials."""
        self.config = {
            "dd_api_key": os.getenv("DD_API_KEY"),
            "dd_app_key": os.getenv("DD_APP_KEY"),
            "site_api_url": os.getenv("DD_SITE_URL", "https://api.datadoghq.eu"),
            "request_timeout": 60,
        }

        self.toolset = DatadogTracesToolset()
        success, error_msg = self.toolset.prerequisites_callable(self.config)
        assert success, f"Failed to initialize toolset: {error_msg}"

    def test_health_check_live(self):
        """Test that the health check passes with valid credentials."""
        success, error_msg = self.toolset.prerequisites_callable(self.config)
        assert success, f"Health check failed: {error_msg}"

    def test_fetch_traces_list_live(self):
        """Test fetching traces from the live Datadog instance."""
        fetch_traces_tool = self.toolset.tools[0]
        assert fetch_traces_tool.name == "fetch_datadog_spans"

        # Fetch traces from the last hour
        params = {
            "start_datetime": "-3600",  # 1 hour ago
            "end_datetime": "0",  # now
            "limit": 10,
        }

        result = fetch_traces_tool._invoke(
            params, context=create_mock_tool_invoke_context()
        )

        assert (
            result.status.value == "success"
        ), f"Failed to fetch traces: {result.error}"
        assert result.data is not None
        # The tool returns raw response data with a 'data' key containing the list of traces
        assert "data" in result.data

    def test_fetch_traces_with_service_filter_live(self):
        """Test fetching traces with service filter."""
        fetch_traces_tool = self.toolset.tools[0]

        # This will likely return no results unless you have a service named "test-service"
        # GetSpans tool uses 'query' parameter, not 'service'
        params = {
            "query": "service:test-service",
            "start_datetime": "-3600",
            "limit": 5,
        }

        result = fetch_traces_tool._invoke(
            params, context=create_mock_tool_invoke_context()
        )

        assert (
            result.status.value == "success"
        ), f"Failed to fetch traces: {result.error}"
        assert result.data is not None

    def test_fetch_spans_by_filter_live(self):
        """Test searching for spans."""
        fetch_spans_tool = self.toolset.tools[0]  # GetSpans is now at index 0
        assert fetch_spans_tool.name == "fetch_datadog_spans"

        # Search for any spans in the last 15 minutes
        params = {
            "start_datetime": "-900",  # 15 minutes ago
            "limit": 10,
        }

        result = fetch_spans_tool._invoke(
            params, context=create_mock_tool_invoke_context()
        )

        assert (
            result.status.value == "success"
        ), f"Failed to fetch spans: {result.error}"
        assert result.data is not None
        # The tool returns raw response data with a 'data' key containing the list of spans
        assert "data" in result.data

    def test_fetch_trace_by_id_live(self):
        """Test fetching a specific trace by ID."""
        # First, try to get some traces to find a valid trace ID
        fetch_traces_tool = self.toolset.tools[0]

        params = {
            "start_datetime": "-3600",
            "limit": 1,
        }

        result = fetch_traces_tool._invoke(
            params, context=create_mock_tool_invoke_context()
        )

        if (
            result.status.value == "success"
            and result.data
            and "data" in result.data
            and len(result.data["data"]) > 0
        ):
            # Extract a trace ID from the first trace in the result
            first_trace = result.data["data"][0]
            if "attributes" in first_trace and "trace_id" in first_trace["attributes"]:
                trace_id = first_trace["attributes"]["trace_id"]

                # We no longer have a fetch_datadog_trace_by_id tool
                # Instead, we can search for the specific trace using GetSpans
                params = {"query": f"trace_id:{trace_id}"}

                result = fetch_traces_tool._invoke(
                    params, context=create_mock_tool_invoke_context()
                )

                assert (
                    result.status.value == "success"
                ), f"Failed to fetch trace: {result.error}"
                # Check that we got data back
                assert result.data and "data" in result.data

    def test_fetch_traces_with_duration_filter_live(self):
        """Test fetching traces with minimum duration filter."""
        fetch_traces_tool = self.toolset.tools[0]

        # Look for traces taking more than 100ms
        # GetSpans tool doesn't have min_duration parameter, use query instead
        params = {
            "query": "@duration:>100000000",  # duration > 100ms in nanoseconds
            "start_datetime": "-3600",
            "limit": 5,
        }

        result = fetch_traces_tool._invoke(
            params, context=create_mock_tool_invoke_context()
        )

        assert (
            result.status.value == "success"
        ), f"Failed to fetch traces: {result.error}"
        assert result.data is not None

    def test_fetch_spans_with_query_live(self):
        """Test searching spans with a Datadog query."""
        fetch_spans_tool = self.toolset.tools[0]  # GetSpans is at index 0

        # Search for any error spans
        params = {
            "query": "status:error",
            "start_datetime": "-3600",
            "limit": 5,
        }

        result = fetch_spans_tool._invoke(
            params, context=create_mock_tool_invoke_context()
        )

        assert (
            result.status.value == "success"
        ), f"Failed to fetch spans: {result.error}"
        assert result.data is not None
