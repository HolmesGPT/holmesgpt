"""Tests that tool output text appears exactly once in format_tool_result_data().

The error field should describe the failure; the data field carries the raw output.
format_tool_result_data() concatenates both, so embedding raw_output in both
error and data causes duplication in the LLM message.
"""

import pytest

from holmes.core.models import format_tool_result_data
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus


TOOL_CALL_ID = "call_test123"
TOOL_NAME = "kubectl_get"


class TestNoOutputDuplication:
    """Verify that raw output text never appears more than once in formatted result."""

    def test_error_path_output_appears_once(self):
        """When a command fails, its raw output should appear exactly once."""
        raw_output = "error: the server doesn't have a resource type 'foobar'"
        invocation = "kubectl get foobar"
        result = StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=f"Command `{invocation}` failed with return code 1",
            return_code=1,
            data=raw_output,
            params={"command": invocation},
            invocation=invocation,
        )

        formatted = format_tool_result_data(
            tool_result=result,
            tool_call_id=TOOL_CALL_ID,
            tool_name=TOOL_NAME,
        )

        count = formatted.count(raw_output)
        assert count == 1, (
            f"Raw output appears {count} times in formatted result, expected exactly 1.\n"
            f"Formatted result:\n{formatted}"
        )

    def test_error_path_multiline_output_appears_once(self):
        """Multi-line error output (e.g. stack traces) should appear exactly once."""
        raw_output = (
            "runtime: out of memory\n"
            "goroutine 1 [running]:\n"
            "main.main()\n"
            "\t/app/main.go:42 +0x1a8\n"
        )
        invocation = "kubectl get -A --show-labels -o wide deployments"
        result = StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=f"Command `{invocation}` failed with return code 2",
            return_code=2,
            data=raw_output,
            params={"command": invocation},
            invocation=invocation,
        )

        formatted = format_tool_result_data(
            tool_result=result,
            tool_call_id=TOOL_CALL_ID,
            tool_name=TOOL_NAME,
        )

        count = formatted.count(raw_output)
        assert count == 1, (
            f"Raw output appears {count} times in formatted result, expected exactly 1.\n"
            f"Formatted result:\n{formatted}"
        )

    def test_success_path_output_appears_once(self):
        """On success, the output should appear exactly once (no error field)."""
        raw_output = "NAME    READY   STATUS    RESTARTS   AGE\nnginx   1/1     Running   0          5m"
        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            error=None,
            return_code=0,
            data=raw_output,
            params={"command": "kubectl get pods"},
            invocation="kubectl get pods",
        )

        formatted = format_tool_result_data(
            tool_result=result,
            tool_call_id=TOOL_CALL_ID,
            tool_name=TOOL_NAME,
        )

        count = formatted.count(raw_output)
        assert count == 1, (
            f"Raw output appears {count} times in formatted result, expected exactly 1.\n"
            f"Formatted result:\n{formatted}"
        )

    def test_error_path_empty_output(self):
        """When a command fails with empty output, error message should appear once."""
        invocation = "kubectl get nodes"
        error_msg = f"Command `{invocation}` failed with return code 137"
        result = StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=error_msg,
            return_code=137,
            data="",
            params={"command": invocation},
            invocation=invocation,
        )

        formatted = format_tool_result_data(
            tool_result=result,
            tool_call_id=TOOL_CALL_ID,
            tool_name=TOOL_NAME,
        )

        count = formatted.count(error_msg)
        assert count == 1, (
            f"Error message appears {count} times, expected exactly 1.\n"
            f"Formatted result:\n{formatted}"
        )

    def test_error_path_invocation_appears_once(self):
        """The command invocation should not be repeated across error and params."""
        invocation = "kubectl get -A --show-labels -o wide deployments"
        raw_output = "Killed"
        result = StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=f"Command `{invocation}` failed with return code 137",
            return_code=137,
            data=raw_output,
            params={"command": invocation},
            invocation=invocation,
        )

        formatted = format_tool_result_data(
            tool_result=result,
            tool_call_id=TOOL_CALL_ID,
            tool_name=TOOL_NAME,
        )

        # The raw tool output "Killed" should appear only once
        count = formatted.count(raw_output)
        assert count == 1, (
            f"Raw output '{raw_output}' appears {count} times, expected exactly 1.\n"
            f"Formatted result:\n{formatted}"
        )

    def test_streaming_response_no_duplication(self):
        """In the streaming response, error and data should not duplicate content."""
        from holmes.core.models import ToolCallResult

        raw_output = "connection refused"
        invocation = "curl http://localhost:8080/health"
        tool_result = StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=f"Command `{invocation}` failed with return code 7",
            return_code=7,
            data=raw_output,
            params={"command": invocation},
            invocation=invocation,
        )

        tcr = ToolCallResult(
            tool_call_id=TOOL_CALL_ID,
            tool_name=TOOL_NAME,
            description="Health check",
            result=tool_result,
        )

        response = tcr.as_streaming_tool_result_response()
        result_dump = response["result"]

        # The raw output should not appear in both error and data
        error_has_output = raw_output in (result_dump.get("error") or "")
        data_has_output = raw_output in (result_dump.get("data") or "")

        assert data_has_output and not error_has_output, (
            f"Raw output '{raw_output}' should appear in data but not in error of streaming response.\n"
            f"error: {result_dump.get('error')}\n"
            f"data: {result_dump.get('data')}"
        )
