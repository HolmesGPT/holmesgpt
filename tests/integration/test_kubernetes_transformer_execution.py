"""
Integration tests for Phase 3.1: Kubernetes tools with transformer execution.
"""

import os
import tempfile
from unittest.mock import patch

from pydantic import Field

from holmes.core.tools import (
    StructuredToolResultStatus,
)
from holmes.core.transformers import registry
from holmes.core.transformers.base import BaseTransformer
from holmes.plugins.toolsets import load_toolsets_from_file
from tests.conftest import create_mock_tool_invoke_context


class MockSummarizeTransformer(BaseTransformer):
    """Mock LLM summarize transformer for testing."""

    # Pydantic fields with validation
    input_threshold: int = Field(
        default=1000, ge=0, description="Minimum input length to trigger summarization"
    )
    prompt: str = Field(
        default="Default summarization prompt", description="Custom prompt for testing"
    )

    def transform(self, input_text: str) -> str:
        # Simulate summarization by always reducing the text size
        # Include original length info that tests expect
        original_length = len(input_text)
        length_info = f"[Original length: {original_length} chars]"

        if len(input_text) <= 50:
            # For short inputs, create a minimal summary
            return f"SUMMARIZED: Short content {length_info}"

        # For longer inputs, reduce to approximately 60% of original size
        target_length = int(len(input_text) * 0.6)
        prefix = "SUMMARIZED: "
        suffix = f"... {length_info}"

        # Calculate how much content we can include
        available_length = target_length - len(prefix) - len(suffix)

        if available_length < 10:
            # If we don't have much space, just use a short summary
            return f"{prefix}Content truncated {length_info}"

        return f"{prefix}{input_text[:available_length]}...{length_info}"

    def should_apply(self, input_text: str) -> bool:
        return len(input_text) >= self.input_threshold

    @property
    def name(self) -> str:
        return "llm_summarize"  # Changed to match kubernetes.yaml transformer name


class TestKubernetesTransformerExecution:
    """Test full execution of Kubernetes tools with transformers."""

    def setup_method(self):
        """Set up test fixtures."""
        # Save original transformer registration if it exists
        self.original_llm_summarize = None
        if registry.is_registered("llm_summarize"):
            self.original_llm_summarize = registry._transformers["llm_summarize"]
            registry.unregister("llm_summarize")

        # Clean up any mock registrations
        if registry.is_registered("MockSummarizeTransformer"):
            registry.unregister("MockSummarizeTransformer")

        # Register mock transformer
        registry.register(MockSummarizeTransformer)

    def teardown_method(self):
        """Clean up test fixtures."""
        # Unregister mock transformer
        if registry.is_registered("llm_summarize"):
            registry.unregister("llm_summarize")
        if registry.is_registered("MockSummarizeTransformer"):
            registry.unregister("MockSummarizeTransformer")

        # Restore original transformer if it existed
        if self.original_llm_summarize is not None:
            registry.register(self.original_llm_summarize)

    def test_kubectl_describe_with_large_output(self):
        """Test kubectl_describe applies transformer for large output."""
        # Load the actual kubernetes.yaml file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        kubernetes_yaml_path = os.path.join(
            current_dir, "..", "..", "holmes", "plugins", "toolsets", "kubernetes.yaml"
        )

        toolsets = load_toolsets_from_file(kubernetes_yaml_path)
        kubernetes_core = next(ts for ts in toolsets if ts.name == "kubernetes/core")
        kubectl_describe = next(
            tool for tool in kubernetes_core.tools if tool.name == "kubectl_describe"
        )

        # Create large kubectl describe output that should trigger transformation
        large_output = (
            """
Name:         test-pod-12345
Namespace:    default
Priority:     0
Node:         node-1/10.0.1.5
Start Time:   Mon, 01 Jan 2024 10:00:00 +0000
Labels:       app=test
              version=1.0.0
Annotations:  deployment.kubernetes.io/revision: 1
Status:       Running
IP:           10.244.0.15
IPs:
  IP:  10.244.0.15
Containers:
  test-container:
    Container ID:   containerd://abc123def456
    Image:          nginx:1.21
    Image ID:       docker-pullable://nginx@sha256:abc123
    Port:           80/TCP
    Host Port:      0/TCP
    State:          Running
      Started:      Mon, 01 Jan 2024 10:01:00 +0000
    Ready:          True
    Restart Count:  0
    Environment:    <none>
    Mounts:
      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-xyz (ro)
Conditions:
  Type              Status
  Initialized       True
  Ready             True
  ContainersReady   True
  PodScheduled      True
Volumes:
  kube-api-access-xyz:
    Type:                    Projected (a volume that contains injected data from multiple sources)
    TokenExpirationSeconds:  3607
    ConfigMapName:           kube-root-ca.crt
Events:
  Type    Reason     Age   From               Message
  ----    ------     ----  ----               -------
  Normal  Scheduled  5m    default-scheduler  Successfully assigned default/test-pod-12345 to node-1
  Normal  Pulling    5m    kubelet            Pulling image "nginx:1.21"
  Normal  Pulled     4m    kubelet            Successfully pulled image "nginx:1.21"
  Normal  Created    4m    kubelet            Created container test-container
  Normal  Started    4m    kubelet            Started container test-container
"""
            * 3
        )  # Repeat to make it large enough to trigger transformation

        # Mock the subprocess execution
        with patch.object(
            kubectl_describe, "_YAMLTool__execute_subprocess"
        ) as mock_subprocess:
            mock_subprocess.return_value = (large_output, 0)

            # Execute the tool
            context = create_mock_tool_invoke_context()
            result = kubectl_describe.invoke(
                {"kind": "pod", "name": "test-pod-12345", "namespace": "default"},
                context,
            )

            # Should have applied transformation
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data is not None
            assert "SUMMARIZED:" in result.data
            assert f"Original length: {len(large_output)}" in result.data
            assert len(result.data) < len(
                large_output
            )  # Should be shorter due to summarization

    def test_kubectl_describe_with_small_output(self):
        """Test kubectl_describe skips transformer for small output."""
        # Load the actual kubernetes.yaml file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        kubernetes_yaml_path = os.path.join(
            current_dir, "..", "..", "holmes", "plugins", "toolsets", "kubernetes.yaml"
        )

        toolsets = load_toolsets_from_file(kubernetes_yaml_path)
        kubernetes_core = next(ts for ts in toolsets if ts.name == "kubernetes/core")
        kubectl_describe = next(
            tool for tool in kubernetes_core.tools if tool.name == "kubectl_describe"
        )

        # Create small output that should NOT trigger transformation
        small_output = "Name: test\nStatus: Running"

        # Mock the subprocess execution
        with patch.object(
            kubectl_describe, "_YAMLTool__execute_subprocess"
        ) as mock_subprocess:
            mock_subprocess.return_value = (small_output, 0)

            # Execute the tool
            context = create_mock_tool_invoke_context()
            result = kubectl_describe.invoke(
                {"kind": "pod", "name": "test-pod", "namespace": "default"}, context
            )

            # Should NOT have applied transformation
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data == small_output
            assert "SUMMARIZED:" not in result.data

    def test_kubectl_logs_with_large_output(self):
        """Test kubectl_logs applies transformer for large log output."""
        # Load the actual kubernetes_logs.yaml file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        kubernetes_logs_yaml_path = os.path.join(
            current_dir,
            "..",
            "..",
            "holmes",
            "plugins",
            "toolsets",
            "kubernetes_logs.yaml",
        )

        toolsets = load_toolsets_from_file(kubernetes_logs_yaml_path)
        kubernetes_logs = next(ts for ts in toolsets if ts.name == "kubernetes/logs")
        kubectl_logs = next(
            tool for tool in kubernetes_logs.tools if tool.name == "kubectl_logs"
        )

        # Create large log output that should trigger transformation
        large_log_output = (
            """
2024-01-01T10:00:01.123Z INFO  Starting application server on port 8080
2024-01-01T10:00:01.234Z INFO  Loading configuration from /app/config.yaml
2024-01-01T10:00:01.345Z INFO  Database connection established to postgresql://db:5432/app
2024-01-01T10:00:01.456Z INFO  Redis cache connection established to redis://cache:6379
2024-01-01T10:00:01.567Z INFO  Metrics endpoint available at /metrics
2024-01-01T10:00:01.678Z INFO  Health check endpoint available at /health
2024-01-01T10:00:01.789Z INFO  Application started successfully
2024-01-01T10:00:02.123Z INFO  Processing request GET /api/users
2024-01-01T10:00:02.234Z INFO  Processing request POST /api/orders
2024-01-01T10:00:02.345Z WARN  High memory usage detected: 85%
2024-01-01T10:00:02.456Z INFO  Processing request GET /api/products
2024-01-01T10:00:02.567Z ERROR Failed to connect to external API: connection timeout
2024-01-01T10:00:02.678Z WARN  Retrying external API connection (attempt 1/3)
2024-01-01T10:00:02.789Z INFO  External API connection restored
"""
            * 20
        )  # Repeat to make it large enough to trigger transformation

        # Mock the subprocess execution
        with patch.object(
            kubectl_logs, "_YAMLTool__execute_subprocess"
        ) as mock_subprocess:
            mock_subprocess.return_value = (large_log_output, 0)

            # Execute the tool
            context = create_mock_tool_invoke_context()
            result = kubectl_logs.invoke(
                {"pod_name": "test-app-12345", "namespace": "default"}, context
            )

            # Should have applied transformation
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data is not None
            assert "SUMMARIZED:" in result.data
            assert f"Original length: {len(large_log_output)}" in result.data
            assert len(result.data) < len(
                large_log_output
            )  # Should be shorter due to summarization

    def test_kubectl_get_cluster_with_transformer(self):
        """Test kubectl_get_by_kind_in_cluster applies transformer for large output."""
        # Load the actual kubernetes.yaml file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        kubernetes_yaml_path = os.path.join(
            current_dir, "..", "..", "holmes", "plugins", "toolsets", "kubernetes.yaml"
        )

        toolsets = load_toolsets_from_file(kubernetes_yaml_path)
        kubernetes_core = next(ts for ts in toolsets if ts.name == "kubernetes/core")
        kubectl_get_cluster = next(
            tool
            for tool in kubernetes_core.tools
            if tool.name == "kubectl_get_by_kind_in_cluster"
        )

        # Create large kubectl get output
        large_get_output = (
            """
NAMESPACE     NAME                                    READY   STATUS    RESTARTS   AGE     IP           NODE     NOMINATED NODE   READINESS GATES   LABELS
kube-system   coredns-558bd4d5db-abc123              1/1     Running   0          10d     10.244.0.2   node-1   <none>           <none>            k8s-app=kube-dns,pod-template-hash=558bd4d5db
kube-system   coredns-558bd4d5db-def456              1/1     Running   0          10d     10.244.0.3   node-1   <none>           <none>            k8s-app=kube-dns,pod-template-hash=558bd4d5db
kube-system   etcd-node-1                            1/1     Running   0          10d     10.0.1.5     node-1   <none>           <none>            component=etcd,tier=control-plane
kube-system   kube-apiserver-node-1                  1/1     Running   0          10d     10.0.1.5     node-1   <none>           <none>            component=kube-apiserver,tier=control-plane
default       nginx-deployment-abc123                1/1     Running   0          5d      10.244.1.5   node-2   <none>           <none>            app=nginx,pod-template-hash=abc123
default       nginx-deployment-def456                1/1     Running   0          5d      10.244.1.6   node-2   <none>           <none>            app=nginx,pod-template-hash=def456
default       redis-master-789                       1/1     Running   0          3d      10.244.1.7   node-2   <none>           <none>            app=redis,role=master
monitoring    prometheus-server-xyz                  1/1     Running   0          2d      10.244.1.8   node-2   <none>           <none>            app=prometheus,component=server
"""
            * 5
        )  # Repeat to make it large enough

        # Mock the subprocess execution
        with patch.object(
            kubectl_get_cluster, "_YAMLTool__execute_subprocess"
        ) as mock_subprocess:
            mock_subprocess.return_value = (large_get_output, 0)

            # Execute the tool
            context = create_mock_tool_invoke_context()
            result = kubectl_get_cluster.invoke({"kind": "pods"}, context)

            # Should have applied transformation
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data is not None
            assert "SUMMARIZED:" in result.data
            assert len(result.data) < len(large_get_output)

    def test_transformer_failure_handling(self):
        """Test that tool execution continues gracefully when transformer fails."""

        # Create a failing transformer
        class FailingTransformer(BaseTransformer):
            def transform(self, input_text: str) -> str:
                raise Exception("Transformer failed")

            def should_apply(self, input_text: str) -> bool:
                return True

            @property
            def name(self) -> str:
                return "failing_summarize"

        # Register failing transformer temporarily
        registry.register(FailingTransformer)

        try:
            # Create a YAML toolset with the failing transformer
            yaml_content = """
toolsets:
  test/failing:
    description: "Test toolset with failing transformer"
    tools:
      - name: "kubectl_failing"
        description: "Tool with failing transformer"
        command: "echo 'test output that should not be transformed'"
        transformers:
          - name: failing_summarize
            config: {}
"""

            # Write to temporary file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as tmp_file:
                tmp_file.write(yaml_content)
                tmp_file_path = tmp_file.name

            try:
                # Load toolsets and get the tool
                toolsets = load_toolsets_from_file(tmp_file_path)
                toolset = toolsets[0]
                tool = toolset.tools[0]

                # Mock subprocess execution
                test_output = "This is test output that should remain unchanged due to transformer failure"
                with patch.object(
                    tool, "_YAMLTool__execute_subprocess"
                ) as mock_subprocess:
                    mock_subprocess.return_value = (test_output, 0)

                    # Execute with logging to catch transformer failure
                    with patch("holmes.core.tools.logger") as mock_logging:
                        context = create_mock_tool_invoke_context()
                        result = tool.invoke({}, context)

                        # Should return original output when transformer fails
                        assert result.status == StructuredToolResultStatus.SUCCESS
                        assert result.data == test_output

                        # Should log error about transformer failure (generic Exception -> error log)
                        mock_logging.error.assert_called()
                        error_call = mock_logging.error.call_args[0][0]
                        assert "failing_summarize" in error_call
                        assert "failed" in error_call

            finally:
                os.unlink(tmp_file_path)

        finally:
            if registry.is_registered("failing_summarize"):
                registry.unregister("failing_summarize")

    def test_transformer_error_status_handling(self):
        """Test that transformers are not applied when tool returns error status."""
        # Load the actual kubernetes.yaml file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        kubernetes_yaml_path = os.path.join(
            current_dir, "..", "..", "holmes", "plugins", "toolsets", "kubernetes.yaml"
        )

        toolsets = load_toolsets_from_file(kubernetes_yaml_path)
        kubernetes_core = next(ts for ts in toolsets if ts.name == "kubernetes/core")
        kubectl_describe = next(
            tool for tool in kubernetes_core.tools if tool.name == "kubectl_describe"
        )

        # Mock subprocess to return error
        error_output = "Error: pod 'nonexistent' not found"
        with patch.object(
            kubectl_describe, "_YAMLTool__execute_subprocess"
        ) as mock_subprocess:
            # For error case, mock the subprocess to return error code
            mock_subprocess.return_value = (error_output, 1)

            # Execute the tool
            context = create_mock_tool_invoke_context()
            result = kubectl_describe.invoke(
                {"kind": "pod", "name": "nonexistent", "namespace": "default"}, context
            )

            # Should NOT have applied transformation due to error status
            assert result.status == StructuredToolResultStatus.ERROR
            assert result.data == error_output
            assert "SUMMARIZED:" not in result.data

    def test_multiple_transformers_chaining(self):
        """Test that multiple transformers are applied in sequence."""

        # Create a second transformer
        class SecondTransformer(BaseTransformer):
            def transform(self, input_text: str) -> str:
                return f"SECOND_TRANSFORM: {input_text}"

            def should_apply(self, input_text: str) -> bool:
                return True

            @property
            def name(self) -> str:
                return "second_transformer"

        registry.register(SecondTransformer)

        try:
            # Create YAML with multiple transformers
            yaml_content = """
toolsets:
  test/multi:
    description: "Test toolset with multiple transformers"
    tools:
      - name: "kubectl_multi"
        description: "Tool with multiple transformers"
        command: "echo 'test'"
        transformers:
          - name: llm_summarize
            config:
              input_threshold: 10  # Low threshold to ensure it triggers
          - name: second_transformer
            config: {}
"""

            # Write to temporary file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as tmp_file:
                tmp_file.write(yaml_content)
                tmp_file_path = tmp_file.name

            try:
                # Load toolsets and get the tool
                toolsets = load_toolsets_from_file(tmp_file_path)
                toolset = toolsets[0]
                tool = toolset.tools[0]

                # Mock subprocess execution with output that will trigger both transformers
                test_output = "This is a longer test output that should trigger both transformers in sequence"

                # Re-register mock transformer after toolset loading (imports may have restored original)
                if registry.is_registered("llm_summarize"):
                    registry.unregister("llm_summarize")
                registry.register(MockSummarizeTransformer)

                with patch.object(
                    tool, "_YAMLTool__execute_subprocess"
                ) as mock_subprocess:
                    mock_subprocess.return_value = (test_output, 0)

                    # Execute the tool
                    context = create_mock_tool_invoke_context()
                    result = tool.invoke({}, context)

                    # Should have applied both transformers in sequence
                    assert result.status == StructuredToolResultStatus.SUCCESS
                    assert result.data is not None
                    assert "SECOND_TRANSFORM:" in result.data
                    assert "SUMMARIZED:" in result.data

            finally:
                os.unlink(tmp_file_path)

        finally:
            if registry.is_registered("second_transformer"):
                registry.unregister("second_transformer")


class TestTransformerPerformanceMetrics:
    """Test performance monitoring and metrics for transformers."""

    def setup_method(self):
        """Set up test fixtures."""
        # Track whether llm_summarize was originally registered for restoration
        self.llm_summarize_was_registered = registry.is_registered("llm_summarize")

        # Clean up existing registrations
        if registry.is_registered("llm_summarize"):
            registry.unregister("llm_summarize")
        if registry.is_registered("MockSummarizeTransformer"):
            registry.unregister("MockSummarizeTransformer")

        # Register mock transformer
        registry.register(MockSummarizeTransformer)

    def teardown_method(self):
        """Clean up test fixtures."""
        # Unregister mock transformer
        if registry.is_registered("llm_summarize"):
            registry.unregister("llm_summarize")
        if registry.is_registered("MockSummarizeTransformer"):
            registry.unregister("MockSummarizeTransformer")

        # Note: Original transformer restoration is not possible without accessing private registry attributes.
        # Test isolation is maintained by cleaning up our mock registrations.

    def test_transformer_performance_logging(self):
        """Test that transformer execution metrics are logged."""
        # Create a YAML toolset for testing
        yaml_content = """
toolsets:
  test/perf:
    description: "Test toolset for performance monitoring"
    tools:
      - name: "kubectl_perf"
        description: "Tool for performance testing"
        command: "echo 'test'"
        transformers:
          - name: llm_summarize
            config:
              input_threshold: 10
"""

        # Write to temporary file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            # Load toolsets and get the tool
            toolsets = load_toolsets_from_file(tmp_file_path)
            toolset = toolsets[0]
            tool = toolset.tools[0]

            # Re-register mock transformer after toolset loading (imports may have restored original)
            if registry.is_registered("llm_summarize"):
                registry.unregister("llm_summarize")
            registry.register(MockSummarizeTransformer)

            # Mock subprocess execution
            test_output = "This is a test output that should trigger the transformer and performance logging"
            with patch.object(tool, "_YAMLTool__execute_subprocess") as mock_subprocess:
                mock_subprocess.return_value = (test_output, 0)

                # Execute with logging to capture performance metrics
                with patch("holmes.core.tools.logger") as mock_logging:
                    context = create_mock_tool_invoke_context()
                    tool.invoke({}, context)

                    # Should have logged transformer application with metrics
                    info_calls = [
                        call[0][0] for call in mock_logging.info.call_args_list
                    ]
                    transformer_log = next(
                        (call for call in info_calls if "Applied transformer" in call),
                        None,
                    )

                    assert transformer_log is not None
                    assert "llm_summarize" in transformer_log
                    assert "kubectl_perf" in transformer_log
                    assert (
                        "size:" in transformer_log
                    )  # Changed from "output size:" to "size:"
                    assert (
                        "chars," in transformer_log
                    )  # Changed from "characters)" to "chars," (from tools.py line 305-306)

        finally:
            os.unlink(tmp_file_path)
