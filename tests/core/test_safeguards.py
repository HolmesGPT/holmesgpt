from holmes.core.safeguards import (
    _has_previous_exact_same_tool_call,
    _has_previous_unfiltered_pod_logs_call,
    _is_redundant_fetch_pod_logs,
    prevent_overly_repeated_tool_call,
)
from holmes.core.tool_calling_llm import ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.plugins.toolsets.logging_utils.logging_api import POD_LOGGING_TOOL_NAME


class TestRedundantFetchPodLogs:
    def test_is_redundant_fetch_pod_logs_with_filter_after_unfiltered_no_data(self):
        # Create a previous unfiltered call that returned no data
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name=POD_LOGGING_TOOL_NAME,
                description="Fetch pod logs",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params={
                        "pod_name": "notification-consumer",
                        "namespace": "services",
                        "start_time": "2024-01-01",
                        "end_time": "2024-01-02",
                    },
                ),
            ).as_tool_result_response()
        ]

        # Current call with filter
        tool_params = {
            "pod_name": "notification-consumer",
            "namespace": "services",
            "filter": "error",
            "start_time": "2024-01-01",
            "end_time": "2024-01-02",
        }

        assert (
            _is_redundant_fetch_pod_logs(
                POD_LOGGING_TOOL_NAME, tool_params, previous_tool_calls
            )
            is True
        )

    def test_is_redundant_fetch_pod_logs_with_filter_after_unfiltered_no_data_no_dates(
        self,
    ):
        # Create a previous unfiltered call that returned no data
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name=POD_LOGGING_TOOL_NAME,
                description="Fetch pod logs",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params={
                        "pod_name": "notification-consumer",
                        "namespace": "services",
                    },
                ),
            ).as_tool_result_response()
        ]

        # Current call with filter
        tool_params = {
            "pod_name": "notification-consumer",
            "namespace": "services",
            "filter": "error",
        }

        assert (
            _is_redundant_fetch_pod_logs(
                POD_LOGGING_TOOL_NAME, tool_params, previous_tool_calls
            )
            is True
        )

    def test_is_redundant_fetch_pod_logs_with_filter_after_unfiltered_has_data(self):
        # Create a previous unfiltered call that returned data
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name=POD_LOGGING_TOOL_NAME,
                description="Fetch pod logs",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="foobar",
                    params={
                        "pod_name": "notification-consumer",
                        "namespace": "services",
                        "start_time": "2024-01-01",
                        "end_time": "2024-01-02",
                    },
                ),
            ).as_tool_result_response()
        ]

        # Current call with filter
        tool_params = {
            "pod_name": "notification-consumer",
            "namespace": "services",
            "filter": "error",
            "start_time": "2024-01-01",
            "end_time": "2024-01-02",
        }

        assert (
            _is_redundant_fetch_pod_logs(
                POD_LOGGING_TOOL_NAME, tool_params, previous_tool_calls
            )
            is False
        )

    def test_is_redundant_fetch_pod_logs_different_pod_name(self):
        # Previous call with different pod name
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name=POD_LOGGING_TOOL_NAME,
                description="Fetch pod logs",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params={
                        "pod_name": "other-pod",
                        "namespace": "services",
                    },
                ),
            ).as_tool_result_response()
        ]

        # Current call with filter but different pod
        tool_params = {
            "pod_name": "notification-consumer",
            "namespace": "services",
            "filter": "error",
        }

        assert (
            _is_redundant_fetch_pod_logs(
                POD_LOGGING_TOOL_NAME, tool_params, previous_tool_calls
            )
            is False
        )

    def test_is_redundant_fetch_pod_logs_no_filter_in_current_call(self):
        # Previous call without filter that returned no data
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name=POD_LOGGING_TOOL_NAME,
                description="Fetch pod logs",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params={
                        "pod_name": "notification-consumer",
                        "namespace": "services",
                    },
                ),
            ).as_tool_result_response()
        ]

        # Current call without filter (not redundant)
        tool_params = {
            "pod_name": "notification-consumer",
            "namespace": "services",
        }

        assert (
            _is_redundant_fetch_pod_logs(
                POD_LOGGING_TOOL_NAME, tool_params, previous_tool_calls
            )
            is False
        )

    def test_is_redundant_fetch_pod_logs_different_tool_name(self):
        previous_tool_calls = []
        tool_params = {"filter": "error"}

        assert (
            _is_redundant_fetch_pod_logs(
                "different_tool", tool_params, previous_tool_calls
            )
            is False
        )


class TestHasPreviousUnfilteredPodLogsCall:
    def test_has_previous_unfiltered_pod_logs_call_found(self):
        # Previous unfiltered call with no data
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name=POD_LOGGING_TOOL_NAME,
                description="Fetch pod logs",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params={
                        "pod_name": "my-pod",
                        "namespace": "default",
                    },
                ),
            ).as_tool_result_response()
        ]

        # Current params with filter
        tool_params = {
            "pod_name": "my-pod",
            "namespace": "default",
            "filter": "error",
        }

        assert (
            _has_previous_unfiltered_pod_logs_call(tool_params, previous_tool_calls)
            is True
        )

    def test_has_previous_unfiltered_pod_logs_call_with_success_status(self):
        # Previous unfiltered call with success (should not match)
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name=POD_LOGGING_TOOL_NAME,
                description="Fetch pod logs",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    params={
                        "pod_name": "my-pod",
                        "namespace": "default",
                    },
                ),
            ).as_tool_result_response()
        ]

        tool_params = {
            "pod_name": "my-pod",
            "namespace": "default",
            "filter": "error",
        }

        assert (
            _has_previous_unfiltered_pod_logs_call(tool_params, previous_tool_calls)
            is False
        )

    def test_has_previous_unfiltered_pod_logs_call_different_namespace(self):
        # Previous call with different namespace
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name=POD_LOGGING_TOOL_NAME,
                description="Fetch pod logs",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params={
                        "pod_name": "my-pod",
                        "namespace": "other-namespace",
                    },
                ),
            ).as_tool_result_response()
        ]

        tool_params = {
            "pod_name": "my-pod",
            "namespace": "default",
            "filter": "error",
        }

        assert (
            _has_previous_unfiltered_pod_logs_call(tool_params, previous_tool_calls)
            is False
        )


class TestHasPreviousExactSameToolCall:
    def test_has_previous_exact_same_tool_call_found(self):
        params = {
            "pod_name": "my-pod",
            "namespace": "default",
            "filter": "error",
        }

        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name="my_tool",
                description="Test tool",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    params=params,
                ),
            ).as_tool_result_response()
        ]

        assert (
            _has_previous_exact_same_tool_call("my_tool", params, previous_tool_calls)
            is True
        )

    def test_has_previous_exact_same_tool_call_different_params(self):
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name="my_tool",
                description="Test tool",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    params={"different": "params"},
                ),
            ).as_tool_result_response()
        ]

        params = {
            "pod_name": "my-pod",
            "namespace": "default",
        }

        assert (
            _has_previous_exact_same_tool_call("my_tool", params, previous_tool_calls)
            is False
        )

    def test_has_previous_exact_same_tool_call_different_tool_name(self):
        params = {
            "pod_name": "my-pod",
            "namespace": "services",
        }

        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name="different_tool",
                description="Test tool",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    params=params,
                ),
            ).as_tool_result_response()
        ]

        assert (
            _has_previous_exact_same_tool_call("my_tool", params, previous_tool_calls)
            is False
        )


class TestPreventOverlyRepeatedToolCall:
    def test_prevent_overly_repeated_tool_call_exact_duplicate(self):
        params = {"pod_name": "my-pod", "namespace": "default"}

        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name="my_tool",
                description="Test tool",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    params=params,
                ),
            ).as_tool_result_response()
        ]

        result = prevent_overly_repeated_tool_call(
            "my_tool", params, previous_tool_calls
        )

        assert result is not None
        assert result.status == StructuredToolResultStatus.ERROR
        assert "already been called" in result.error

    def test_prevent_overly_repeated_tool_call_redundant_pod_logs(self):
        # Previous unfiltered call with no data
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name=POD_LOGGING_TOOL_NAME,
                description="Fetch pod logs",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params={
                        "pod_name": "my-pod",
                        "namespace": "default",
                    },
                ),
            ).as_tool_result_response()
        ]

        # Current call with filter
        params = {
            "pod_name": "my-pod",
            "namespace": "default",
            "filter": "error",
        }

        result = prevent_overly_repeated_tool_call(
            POD_LOGGING_TOOL_NAME, params, previous_tool_calls
        )

        assert result is not None
        assert result.status == StructuredToolResultStatus.ERROR
        assert result.error
        assert "without filter has already run" in result.error

    def test_prevent_overly_repeated_tool_call_allowed_call(self):
        # No previous calls
        previous_tool_calls = []
        params = {"pod_name": "my-pod", "namespace": "default"}

        result = prevent_overly_repeated_tool_call(
            "my_tool", params, previous_tool_calls
        )

        assert result is None  # Should return None for allowed calls

    def test_prevent_overly_repeated_tool_call_different_params_allowed(self):
        # Previous call with different params
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name="my_tool",
                description="Test tool",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    params={"different": "params"},
                ),
            ).as_tool_result_response()
        ]

        params = {"pod_name": "my-pod", "namespace": "default"}

        result = prevent_overly_repeated_tool_call(
            "my_tool", params, previous_tool_calls
        )

        assert result is None  # Should return None for allowed calls


class TestEdgeCases:
    def test_empty_tool_calls_list(self):
        params = {"pod_name": "my-pod", "namespace": "default"}

        assert _has_previous_exact_same_tool_call("my_tool", params, []) is False
        assert _has_previous_unfiltered_pod_logs_call(params, []) is False
        assert prevent_overly_repeated_tool_call("my_tool", params, []) is None

    def test_multiple_previous_calls(self):
        # Test with multiple previous calls, one matching
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name="other_tool",
                description="Other tool",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    params={"different": "params"},
                ),
            ).as_tool_result_response(),
            ToolCallResult(
                tool_call_id="2",
                tool_name="my_tool",
                description="My tool",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    params={"pod_name": "my-pod"},
                ),
            ).as_tool_result_response(),
        ]

        assert (
            _has_previous_exact_same_tool_call(
                "my_tool", {"pod_name": "my-pod"}, previous_tool_calls
            )
            is True
        )
