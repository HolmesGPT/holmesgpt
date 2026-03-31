from holmes.core.safeguards import (
    _has_previous_exact_same_tool_call,
    prevent_overly_repeated_tool_call,
)
from holmes.core.tool_calling_llm import ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus


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
            ).to_client_dict()
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
            ).to_client_dict()
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
            ).to_client_dict()
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
            ).to_client_dict()
        ]

        result = prevent_overly_repeated_tool_call(
            "my_tool", params, previous_tool_calls
        )

        assert result is not None
        assert result.status == StructuredToolResultStatus.ERROR
        assert "already been called" in result.error

    def test_prevent_overly_repeated_tool_call_allowed_call(self):
        previous_tool_calls = []
        params = {"pod_name": "my-pod", "namespace": "default"}

        result = prevent_overly_repeated_tool_call(
            "my_tool", params, previous_tool_calls
        )

        assert result is None

    def test_prevent_overly_repeated_tool_call_different_params_allowed(self):
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name="my_tool",
                description="Test tool",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    params={"different": "params"},
                ),
            ).to_client_dict()
        ]

        params = {"pod_name": "my-pod", "namespace": "default"}

        result = prevent_overly_repeated_tool_call(
            "my_tool", params, previous_tool_calls
        )

        assert result is None


class TestEdgeCases:
    def test_empty_tool_calls_list(self):
        params = {"pod_name": "my-pod", "namespace": "default"}

        assert _has_previous_exact_same_tool_call("my_tool", params, []) is False
        assert prevent_overly_repeated_tool_call("my_tool", params, []) is None

    def test_multiple_previous_calls(self):
        previous_tool_calls = [
            ToolCallResult(
                tool_call_id="1",
                tool_name="other_tool",
                description="Other tool",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    params={"different": "params"},
                ),
            ).to_client_dict(),
            ToolCallResult(
                tool_call_id="2",
                tool_name="my_tool",
                description="My tool",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    params={"pod_name": "my-pod"},
                ),
            ).to_client_dict(),
        ]

        assert (
            _has_previous_exact_same_tool_call(
                "my_tool", {"pod_name": "my-pod"}, previous_tool_calls
            )
            is True
        )
