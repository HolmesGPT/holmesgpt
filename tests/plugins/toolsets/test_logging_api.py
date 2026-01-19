"""Tests for the logging API, specifically the PodLoggingTool behavior."""

import os
from unittest.mock import MagicMock

import pytest

from holmes.core.llm import DefaultLLM
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.core.tools_utils.token_counting import count_tool_response_tokens
from holmes.plugins.toolsets.logging_utils.logging_api import (
    TRUNCATION_PROMPT_PREFIX,
    BasePodLoggingToolset,
    FetchPodLogsParams,
    LoggingCapability,
    PodLoggingTool,
    truncate_logs,
)
from tests.conftest import create_mock_tool_invoke_context


class TestPodLoggingTool:
    """Test PodLoggingTool behavior with different input types."""

    def test_tool_handles_integer_start_time(self):
        """Test that PodLoggingTool correctly handles integer start_time values."""
        # Create mock toolset
        mock_toolset = MagicMock(spec=BasePodLoggingToolset)
        mock_toolset.name = "test-logging-backend"
        mock_toolset.supported_capabilities = set()
        mock_toolset.fetch_pod_logs.return_value = StructuredToolResult(
            data="Sample logs", status=StructuredToolResultStatus.SUCCESS
        )

        # Create the tool
        tool = PodLoggingTool(mock_toolset)

        # Call tool with integer start_time
        params = {
            "namespace": "default",
            "pod_name": "test-pod",
            "start_time": -300,  # Integer!
        }
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        # Verify the result
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "Sample logs"

        # Verify toolset.fetch_pod_logs was called once
        mock_toolset.fetch_pod_logs.assert_called_once()

        # Get the actual params passed to fetch_pod_logs
        call_args = mock_toolset.fetch_pod_logs.call_args
        actual_params = call_args.kwargs["params"]

        # Verify it's a FetchPodLogsParams instance with string start_time
        assert isinstance(actual_params, FetchPodLogsParams)
        assert actual_params.namespace == "default"
        assert actual_params.pod_name == "test-pod"
        assert actual_params.start_time == "-300"  # Converted to string!
        assert isinstance(actual_params.start_time, str)

    def test_tool_handles_string_start_time(self):
        """Test that PodLoggingTool correctly handles string start_time values."""
        # Create mock toolset
        mock_toolset = MagicMock(spec=BasePodLoggingToolset)
        mock_toolset.name = "test-logging-backend"
        mock_toolset.supported_capabilities = set()
        mock_toolset.fetch_pod_logs.return_value = StructuredToolResult(
            data="Sample logs", status=StructuredToolResultStatus.SUCCESS
        )

        # Create the tool
        tool = PodLoggingTool(mock_toolset)

        # Call tool with string start_time
        params = {
            "namespace": "production",
            "pod_name": "api-server",
            "start_time": "-600",  # Already a string
        }
        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        # Verify the result
        assert result.status == StructuredToolResultStatus.SUCCESS

        # Get the actual params passed to fetch_pod_logs
        call_args = mock_toolset.fetch_pod_logs.call_args
        actual_params = call_args.kwargs["params"]

        # Verify start_time remains a string
        assert actual_params.start_time == "-600"
        assert isinstance(actual_params.start_time, str)

    def test_tool_handles_rfc3339_start_time(self):
        """Test that PodLoggingTool correctly handles RFC3339 formatted start_time."""
        # Create mock toolset
        mock_toolset = MagicMock(spec=BasePodLoggingToolset)
        mock_toolset.name = "test-logging-backend"
        mock_toolset.supported_capabilities = set()
        mock_toolset.fetch_pod_logs.return_value = StructuredToolResult(
            data="Sample logs", status=StructuredToolResultStatus.SUCCESS
        )

        # Create the tool
        tool = PodLoggingTool(mock_toolset)

        # Call tool with RFC3339 formatted start_time
        params = {
            "namespace": "staging",
            "pod_name": "web-app",
            "start_time": "2023-03-01T10:30:00Z",  # RFC3339 format
        }
        tool._invoke(params, context=create_mock_tool_invoke_context())

        # Get the actual params passed to fetch_pod_logs
        call_args = mock_toolset.fetch_pod_logs.call_args
        actual_params = call_args.kwargs["params"]

        # Verify start_time remains unchanged
        assert actual_params.start_time == "2023-03-01T10:30:00Z"
        assert isinstance(actual_params.start_time, str)

    def test_tool_handles_no_start_time(self):
        """Test that PodLoggingTool correctly handles missing start_time."""
        # Create mock toolset
        mock_toolset = MagicMock(spec=BasePodLoggingToolset)
        mock_toolset.name = "test-logging-backend"
        mock_toolset.supported_capabilities = set()
        mock_toolset.fetch_pod_logs.return_value = StructuredToolResult(
            data="Sample logs", status=StructuredToolResultStatus.SUCCESS
        )

        # Create the tool
        tool = PodLoggingTool(mock_toolset)

        # Call tool without start_time
        params = {"namespace": "kube-system", "pod_name": "coredns"}
        tool._invoke(params, context=create_mock_tool_invoke_context())

        # Get the actual params passed to fetch_pod_logs
        call_args = mock_toolset.fetch_pod_logs.call_args
        actual_params = call_args.kwargs["params"]

        # Verify start_time is None
        assert actual_params.start_time is None

    def test_tool_with_all_parameters(self):
        """Test that all parameters are correctly passed through."""
        # Create mock toolset with capabilities
        mock_toolset = MagicMock(spec=BasePodLoggingToolset)
        mock_toolset.name = "test-logging-backend"
        mock_toolset.supported_capabilities = {
            LoggingCapability.REGEX_FILTER,
            LoggingCapability.EXCLUDE_FILTER,
        }
        mock_toolset.fetch_pod_logs.return_value = StructuredToolResult(
            data="Filtered logs", status=StructuredToolResultStatus.SUCCESS
        )

        # Create the tool
        tool = PodLoggingTool(mock_toolset)

        # Call tool with all parameters, including integer start_time
        params = {
            "namespace": "monitoring",
            "pod_name": "prometheus-0",
            "start_time": -7200,  # Integer - 2 hours ago
            "end_time": "2023-03-01T12:00:00Z",
            "filter": "error|warning",
            "exclude_filter": "health",
            "limit": 50,
        }
        tool._invoke(params, context=create_mock_tool_invoke_context())

        # Get the actual params passed to fetch_pod_logs
        call_args = mock_toolset.fetch_pod_logs.call_args
        actual_params = call_args.kwargs["params"]

        # Verify all parameters, especially start_time conversion
        assert actual_params.namespace == "monitoring"
        assert actual_params.pod_name == "prometheus-0"
        assert actual_params.start_time == "-7200"  # Converted to string!
        assert isinstance(actual_params.start_time, str)
        assert actual_params.end_time == "2023-03-01T12:00:00Z"
        assert actual_params.filter == "error|warning"
        assert actual_params.exclude_filter == "health"
        assert actual_params.limit == 50


class TestTruncateLogs:
    """Test truncate_logs function behavior."""

    def test_truncate_logs_nominal_scenario(self):
        """Test that truncate_logs correctly truncates logs when they exceed the token limit."""
        # Create a mock LLM that simulates token counting

        model = os.environ.get("MODEL")
        if not model:
            pytest.skip("Missing MODEL env var.")
        llm = DefaultLLM(model=model)

        log_message = "ERROR: Database connection failed\n"
        log_data = log_message * 2000  # Long log data

        structured_result = StructuredToolResult(
            data=log_data, status=StructuredToolResultStatus.SUCCESS
        )

        token_limit = 1000
        tool_call_id = "test_call_123"
        tool_name = "test_tool"

        truncate_logs(
            logging_structured_tool_result=structured_result,
            llm=llm,
            token_limit=token_limit,
            structured_params=FetchPodLogsParams(pod_name="dummy", namespace="dummy"),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )

        truncated_log_data = str(structured_result.data)

        assert len(truncated_log_data) < len(log_data)

        assert truncated_log_data.startswith(TRUNCATION_PROMPT_PREFIX)

        truncated_log_data_without_prefix = truncated_log_data[
            len(TRUNCATION_PROMPT_PREFIX) :
        ].strip()
        assert truncated_log_data_without_prefix.startswith(
            log_message
        )  # Ensures the log line following the truncation prefix is not cut in half

        truncated_token_count = count_tool_response_tokens(
            structured_tool_result=structured_result,
            llm=llm,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )
        assert truncated_token_count < token_limit

        # Expect the final token count to be within N% of the limit
        expected_token_count_lower_limit = token_limit * 0.95
        assert expected_token_count_lower_limit < truncated_token_count
