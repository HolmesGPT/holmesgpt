"""
Integration tests for Phase 2.2: Tool Execution Pipeline with transformers.

These tests verify the complete tool execution flow with transformers
in a more realistic environment.
"""

import time
from typing import Dict
from unittest.mock import patch

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    YAMLTool,
)
from holmes.core.transformers import (
    BaseTransformer,
    Transformer,
    TransformerError,
    registry,
)
from tests.conftest import create_mock_tool_invoke_context


class MockLLMSummarizeTransformer(BaseTransformer):
    """Mock LLM summarize transformer for integration testing."""

    input_threshold: int = 100

    def transform(self, input_text: str) -> str:
        # Simulate LLM summarization by shortening the text
        words = input_text.split()
        if len(words) > 10:
            return f"SUMMARIZED: {' '.join(words[:5])}... (truncated {len(words) - 5} words)"
        return input_text

    def should_apply(self, input_text: str) -> bool:
        # Apply threshold logic - only transform if input is long enough
        return len(input_text) >= self.input_threshold

    @property
    def name(self) -> str:
        return "llm_summarize"


class TestToolExecutionPipelineIntegration:
    """Integration tests for complete tool execution pipeline."""

    def setup_method(self):
        """Set up test fixtures."""
        # Save original state for restoration
        self._original_llm_summarize = None
        if registry.is_registered("llm_summarize"):
            # Store the original transformer class for restoration
            # Use create_transformer to verify it exists and get the class reference
            try:
                test_instance = registry.create_transformer("llm_summarize", {})
                self._original_llm_summarize = test_instance.__class__
            except Exception:
                self._original_llm_summarize = None
            registry.unregister("llm_summarize")

        # Register our mock transformer
        registry.register(MockLLMSummarizeTransformer)

    def teardown_method(self):
        """Clean up test fixtures."""
        # Clean up our mock transformer
        if registry.is_registered("llm_summarize"):
            registry.unregister("llm_summarize")

        # Restore original transformer if it existed
        if self._original_llm_summarize is not None:
            registry.register(self._original_llm_summarize)

    def test_yaml_tool_with_transformer_integration(self):
        """Test complete YAML tool execution with transformer."""
        tool = YAMLTool(
            name="test_yaml_tool",
            description="Test YAML tool with transformer",
            command="echo 'This is a very long output from a kubectl command that would normally overwhelm the LLM context window with unnecessary details and verbose information that should be summarized'",
            transformers=[
                Transformer(name="llm_summarize", config={"input_threshold": 50})
            ],
        )

        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)

        # Should have executed successfully
        assert result.status == StructuredToolResultStatus.SUCCESS

        # Should have applied transformation (output should be summarized)
        assert result.data is not None
        assert "SUMMARIZED:" in result.data
        assert "truncated" in result.data

        # Original long text should not be in the result
        assert len(result.data) < 200  # Should be much shorter than original

    def test_python_tool_with_multiple_transformers_integration(self):
        """Test Python tool with multiple transformers in sequence."""

        class LogReaderTool(Tool):
            """Simulates a tool that reads large log files."""

            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                # Simulate reading a large log file
                log_entries = [
                    "2024-01-01 10:00:00 INFO Starting application",
                    "2024-01-01 10:00:01 INFO Loading configuration",
                    "2024-01-01 10:00:02 DEBUG Database connection established",
                    "2024-01-01 10:00:03 INFO Server listening on port 8080",
                    "2024-01-01 10:01:00 ERROR Failed to process request: timeout",
                    "2024-01-01 10:01:01 WARN Retrying request",
                    "2024-01-01 10:01:02 INFO Request processed successfully",
                ] * 20  # Repeat to make it large

                large_log = "\n".join(log_entries)

                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=large_log,
                    invocation="tail -n 1000 /var/log/app.log",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "tail -n 1000 /var/log/app.log"

        tool = LogReaderTool(
            name="log_reader",
            description="Read application logs",
            transformers=[
                Transformer(name="llm_summarize", config={"input_threshold": 100})
            ],
        )

        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)

        # Should have executed successfully
        assert result.status == StructuredToolResultStatus.SUCCESS

        # Should have applied transformation
        assert result.data is not None
        assert "SUMMARIZED:" in result.data

        # Should be much shorter than original
        # Calculate size based on the actual test data: 7 different log entries * 20 repetitions
        log_entries = [
            "2024-01-01 10:00:00 INFO Starting application",
            "2024-01-01 10:00:01 INFO Loading configuration",
            "2024-01-01 10:00:02 DEBUG Database connection established",
            "2024-01-01 10:00:03 INFO Server listening on port 8080",
            "2024-01-01 10:01:00 ERROR Failed to process request: timeout",
            "2024-01-01 10:01:01 WARN Retrying request",
            "2024-01-01 10:01:02 INFO Request processed successfully",
        ] * 20
        original_size = len("\n".join(log_entries))
        assert len(result.data) < original_size / 2

    def test_transformer_failure_recovery_integration(self):
        """Test that tool execution continues when transformer fails."""

        class FailingTransformer(BaseTransformer):
            def transform(self, input_text: str) -> str:
                raise TransformerError("Simulated transformer failure")

            def should_apply(self, input_text: str) -> bool:
                return True

            @property
            def name(self) -> str:
                return "failing_transformer"

        registry.register(FailingTransformer)

        try:
            tool = YAMLTool(
                name="test_tool",
                description="Test tool with failing transformer",
                command="echo 'This is test output that should remain unchanged due to transformer failure'",
                transformers=[
                    Transformer(name="failing_transformer", config={}),
                    Transformer(
                        name="llm_summarize", config={"input_threshold": 10}
                    ),  # This should still work
                ],
            )

            with patch("holmes.core.tools.logger") as mock_logging:
                context = create_mock_tool_invoke_context()
                result = tool.invoke({}, context)

                # Tool execution should still succeed
                assert result.status == StructuredToolResultStatus.SUCCESS

                # Should contain original output since first transformer failed
                # but second transformer should still be applied
                assert "This is test output" in result.data

                # Should log warning about transformer failure
                warning_calls = [call for call in mock_logging.warning.call_args_list]
                assert any("failing_transformer" in str(call) for call in warning_calls)

        finally:
            if registry.is_registered("failing_transformer"):
                registry.unregister("failing_transformer")

    def test_conditional_transformer_application_integration(self):
        """Test that transformers are conditionally applied based on content."""
        tool = YAMLTool(
            name="conditional_tool",
            description="Tool with conditional transformer",
            command="echo 'Short'",  # Short output
            transformers=[
                Transformer(
                    name="llm_summarize", config={"input_threshold": 100}
                )  # Higher than output length
            ],
        )

        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)

        # Should execute successfully
        assert result.status == StructuredToolResultStatus.SUCCESS

        # Should NOT have applied transformation (output too short)
        assert "SUMMARIZED:" not in result.data
        assert result.data.strip() == "Short"

    def test_transformer_performance_monitoring_integration(self):
        """Test that transformer performance is properly monitored and logged."""

        class SlowTransformer(BaseTransformer):
            def transform(self, input_text: str) -> str:
                time.sleep(0.1)  # Simulate slow transformation
                return f"SLOW_TRANSFORMED: {input_text}"

            def should_apply(self, input_text: str) -> bool:
                return True

            @property
            def name(self) -> str:
                return "slow_transformer"

        registry.register(SlowTransformer)

        try:
            tool = YAMLTool(
                name="performance_test_tool",
                description="Tool for testing transformer performance monitoring",
                command="echo 'Test output for performance monitoring of transformer execution'",
                transformers=[Transformer(name="slow_transformer", config={})],
            )

            with patch("holmes.core.tools.logger") as mock_logging:
                context = create_mock_tool_invoke_context()
                result = tool.invoke({}, context)

                # Should execute successfully
                assert result.status == StructuredToolResultStatus.SUCCESS

                # Should have applied transformation
                assert "SLOW_TRANSFORMED:" in result.data

                # Should log performance metrics
                info_calls = [call[0][0] for call in mock_logging.info.call_args_list]
                performance_log = next(
                    (call for call in info_calls if "Applied transformer" in call), None
                )

                assert performance_log is not None
                assert "slow_transformer" in performance_log
                assert "performance_test_tool" in performance_log
                assert (
                    " in " in performance_log and "s " in performance_log
                )  # Should show elapsed time pattern like "in X.XXs"
                assert "size:" in performance_log  # Should show size information

        finally:
            if registry.is_registered("slow_transformer"):
                registry.unregister("slow_transformer")

    def test_real_world_kubectl_scenario_integration(self):
        """Test a realistic kubectl-like scenario with large output."""

        # Simulate kubectl get pods output
        kubectl_output = (
            """NAME                                READY   STATUS    RESTARTS   AGE
app-deployment-5d7c9b9b9-abc12     1/1     Running   0          2d
app-deployment-5d7c9b9b9-def34     1/1     Running   0          2d
app-deployment-5d7c9b9b9-ghi56     1/1     Running   0          2d
database-pod-7f8d5c6b4-jkl78       1/1     Running   1          5d
nginx-ingress-controller-mno90     1/1     Running   0          10d
prometheus-server-pqr12            1/1     Running   0          7d
grafana-dashboard-stu34            1/1     Running   0          7d
elasticsearch-cluster-vwx56        1/1     Running   0          3d
kibana-dashboard-yz789             1/1     Running   0          3d
redis-cache-abc123                 1/1     Running   0          1d"""
            * 5
        )  # Repeat to make it larger

        tool = YAMLTool(
            name="kubectl_get_pods",
            description="Get all pods in namespace",
            command=f"echo '{kubectl_output}'",
            transformers=[
                Transformer(name="llm_summarize", config={"input_threshold": 200})
            ],
        )

        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)

        # Should execute successfully
        assert result.status == StructuredToolResultStatus.SUCCESS

        # Should have applied transformation (output should be summarized)
        assert "SUMMARIZED:" in result.data

        # Should preserve important information structure
        assert len(result.data) < len(kubectl_output)  # Much shorter than original

    def test_error_handling_preserves_debugging_info_integration(self):
        """Test that original output is preserved for debugging when transformers fail."""
        tool = YAMLTool(
            name="debug_tool",
            description="Tool for testing debug preservation",
            command="echo 'Important debugging information that should not be lost and contains many additional details about the system state and error conditions that occurred during processing'",
            transformers=[
                Transformer(name="llm_summarize", config={"input_threshold": 10})
            ],
        )

        # Even if transformer modifies the output, the result structure should be preserved
        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.invocation is not None  # Debug info preserved
        assert "echo" in result.invocation  # Original command preserved

        # Transformation applied but structure preserved
        assert "SUMMARIZED:" in result.data
        assert "Important debugging information" in result.data
