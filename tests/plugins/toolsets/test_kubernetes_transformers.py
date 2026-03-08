"""
Unit tests for Phase 3.1: Kubernetes YAML tools with transformer configurations.
"""

import os
import tempfile
from unittest.mock import patch

from holmes.plugins.toolsets import load_toolsets_from_file

from .transformer_test_utils import ensure_transformers_registered


class TestKubernetesYAMLTransformers:
    """Test that Kubernetes YAML tools correctly parse transformer configurations."""

    def test_load_kubernetes_yaml_with_transformers(self):
        """Test loading the actual kubernetes.yaml file with transformers."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        # Find the actual kubernetes.yaml file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        kubernetes_yaml_path = os.path.join(
            current_dir,
            "..",
            "..",
            "..",
            "holmes",
            "plugins",
            "toolsets",
            "kubernetes.yaml",
        )

        # Load toolsets from the file
        toolsets = load_toolsets_from_file(kubernetes_yaml_path)

        # Find the kubernetes/core toolset
        kubernetes_core = None
        for toolset in toolsets:
            if toolset.name == "kubernetes/core":
                kubernetes_core = toolset
                break

        assert kubernetes_core is not None, "kubernetes/core toolset not found"

        # Test kubernetes_jq_query has transformer config
        kubernetes_jq_query = None
        for tool in kubernetes_core.tools:
            if tool.name == "kubernetes_jq_query":
                kubernetes_jq_query = tool
                break

        assert (
            kubernetes_jq_query is not None
        ), "kubernetes_jq_query tool not found"
        assert kubernetes_jq_query.transformers is not None
        assert len(kubernetes_jq_query.transformers) == 1
        assert kubernetes_jq_query.transformers[0].name == "llm_summarize"

        # Test kubernetes_tabular_query has transformer config
        kubernetes_tabular_query = None
        for tool in kubernetes_core.tools:
            if tool.name == "kubernetes_tabular_query":
                kubernetes_tabular_query = tool
                break

        assert (
            kubernetes_tabular_query is not None
        ), "kubernetes_tabular_query tool not found"
        assert kubernetes_tabular_query.transformers is not None
        assert len(kubernetes_tabular_query.transformers) == 1
        assert kubernetes_tabular_query.transformers[0].name == "llm_summarize"

    def test_load_kubernetes_logs_yaml_with_transformers(self):
        """Test loading the kubernetes_logs.yaml file with transformers."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        # Find the actual kubernetes_logs.yaml file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        kubernetes_logs_yaml_path = os.path.join(
            current_dir,
            "..",
            "..",
            "..",
            "holmes",
            "plugins",
            "toolsets",
            "kubernetes_logs.yaml",
        )

        # Load toolsets from the file
        toolsets = load_toolsets_from_file(kubernetes_logs_yaml_path)

        # Find the kubernetes/logs toolset
        kubernetes_logs = None
        for toolset in toolsets:
            if toolset.name == "kubernetes/logs":
                kubernetes_logs = toolset
                break

        assert kubernetes_logs is not None, "kubernetes/logs toolset not found"

        # Test kubectl_logs has transformer config
        kubectl_logs = None
        for tool in kubernetes_logs.tools:
            if tool.name == "kubectl_logs":
                kubectl_logs = tool
                break

        assert kubectl_logs is not None, "kubectl_logs tool not found"
        assert kubectl_logs.transformers is not None
        assert len(kubectl_logs.transformers) == 1
        assert kubectl_logs.transformers[0].name == "llm_summarize"
        assert kubectl_logs.transformers[0].config["input_threshold"] == 1000

        # Test kubectl_logs_all_containers has transformer config
        kubectl_logs_all = None
        for tool in kubernetes_logs.tools:
            if tool.name == "kubectl_logs_all_containers":
                kubectl_logs_all = tool
                break

        assert (
            kubectl_logs_all is not None
        ), "kubectl_logs_all_containers tool not found"
        assert kubectl_logs_all.transformers is not None
        assert len(kubectl_logs_all.transformers) == 1
        assert kubectl_logs_all.transformers[0].name == "llm_summarize"
        assert kubectl_logs_all.transformers[0].config["input_threshold"] == 1000

    def test_yaml_transformer_parsing(self):
        """Test YAML parsing of transformer configurations with various options."""
        yaml_content = """
toolsets:
  test/kubernetes:
    description: "Test Kubernetes toolset with transformers"
    tools:
      - name: "kubectl_test_basic"
        description: "Basic test tool"
        command: "kubectl get pods"
        transformers:
          - name: llm_summarize
            config:
              input_threshold: 500

      - name: "kubectl_test_custom_prompt"
        description: "Test tool with custom prompt"
        command: "kubectl describe pod test"
        transformers:
          - name: llm_summarize
            config:
              input_threshold: 1000
              prompt: |
                Custom summarization prompt for testing:
                - Focus on errors
                - Group similar items

      - name: "kubectl_test_no_transform"
        description: "Test tool without transformers"
        command: "kubectl get nodes"
"""

        # Write to temporary file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            # Load toolsets from the temporary file
            toolsets = load_toolsets_from_file(tmp_file_path)

            assert len(toolsets) == 1
            toolset = toolsets[0]
            assert toolset.name == "test/kubernetes"
            assert len(toolset.tools) == 3

            # Test basic transformer config
            basic_tool = toolset.tools[0]
            assert basic_tool.name == "kubectl_test_basic"
            assert basic_tool.transformers is not None
            assert len(basic_tool.transformers) == 1
            config = basic_tool.transformers[0].config
            assert config["input_threshold"] == 500
            assert "prompt" not in config  # Should use default prompt

            # Test custom prompt transformer config
            custom_tool = toolset.tools[1]
            assert custom_tool.name == "kubectl_test_custom_prompt"
            assert custom_tool.transformers is not None
            config = custom_tool.transformers[0].config
            assert config["input_threshold"] == 1000
            assert "Custom summarization prompt for testing:" in config["prompt"]

            # Test tool without transformers
            no_transform_tool = toolset.tools[2]
            assert no_transform_tool.name == "kubectl_test_no_transform"
            assert no_transform_tool.transformers is None

        finally:
            # Clean up temporary file
            os.unlink(tmp_file_path)

    def test_toolset_level_transformer_inheritance(self):
        """Test that tools inherit transformers from toolset level."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        yaml_content = """
toolsets:
  test/kubernetes:
    description: "Test Kubernetes toolset with toolset-level transformers"
    transformers:
      - name: llm_summarize
        config:
          input_threshold: 800
          prompt: "Toolset default prompt"
    tools:
      - name: "kubectl_inherit"
        description: "Tool that inherits toolset transformers"
        command: "kubectl get pods"

      - name: "kubectl_override"
        description: "Tool that overrides toolset transformers"
        command: "kubectl describe pod test"
        transformers:
          - name: llm_summarize
            config:
              input_threshold: 1200
              prompt: "Tool-specific override prompt"
"""

        # Write to temporary file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            # Load toolsets from the temporary file
            toolsets = load_toolsets_from_file(tmp_file_path)

            assert len(toolsets) == 1
            toolset = toolsets[0]
            assert len(toolset.tools) == 2

            # Test tool that inherits from toolset
            inherit_tool = toolset.tools[0]
            assert inherit_tool.name == "kubectl_inherit"
            assert inherit_tool.transformers is not None
            config = inherit_tool.transformers[0].config
            assert config["input_threshold"] == 800
            assert config["prompt"] == "Toolset default prompt"

            # Test tool that overrides toolset config
            override_tool = toolset.tools[1]
            assert override_tool.name == "kubectl_override"
            assert override_tool.transformers is not None
            config = override_tool.transformers[0].config
            assert config["input_threshold"] == 1200
            assert config["prompt"] == "Tool-specific override prompt"

        finally:
            # Clean up temporary file
            os.unlink(tmp_file_path)

    def test_backward_compatibility_no_transformers(self):
        """Test that existing YAML files without transformers still work."""
        yaml_content = """
toolsets:
  test/legacy:
    description: "Legacy toolset without transformers"
    tools:
      - name: "kubectl_legacy"
        description: "Legacy tool"
        command: "kubectl get pods"

      - name: "kubectl_legacy_2"
        description: "Another legacy tool"
        command: "kubectl get nodes"
"""

        # Write to temporary file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            # Load toolsets from the temporary file
            toolsets = load_toolsets_from_file(tmp_file_path)

            assert len(toolsets) == 1
            toolset = toolsets[0]
            assert toolset.name == "test/legacy"
            assert toolset.transformers is None

            # All tools should have no transformers
            for tool in toolset.tools:
                assert tool.transformers is None

        finally:
            # Clean up temporary file
            os.unlink(tmp_file_path)

    def test_invalid_transformer_handling(self):
        """Test that invalid transformers are handled gracefully."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        yaml_content = """
toolsets:
  test/invalid:
    description: "Test toolset with invalid transformer config"
    tools:
      - name: "kubectl_invalid"
        description: "Tool with invalid transformer"
        command: "kubectl get pods"
        transformers:
          - name: unknown_transformer
            config:
              some_param: "value"
"""

        # Write to temporary file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            # Load toolsets from the temporary file
            with patch("holmes.core.tools.logging") as mock_tools_logging, patch(
                "holmes.core.transformers.transformer.logging"
            ) as mock_transformer_logging:
                toolsets = load_toolsets_from_file(tmp_file_path)

                assert len(toolsets) == 1
                toolset = toolsets[0]

                # Tool should have been created but with cleared transformers
                tool = toolset.tools[0]
                assert tool.name == "kubectl_invalid"
                assert (
                    tool.transformers is None
                )  # Should be cleared due to validation failure

                # Should have logged warning about invalid config in either module
                assert (
                    mock_tools_logging.warning.called
                    or mock_transformer_logging.warning.called
                )

        finally:
            # Clean up temporary file
            os.unlink(tmp_file_path)

    def test_multiple_transformers_in_yaml(self):
        """Test YAML parsing with multiple transformers per tool."""
        yaml_content = """
toolsets:
  test/multi:
    description: "Test toolset with multiple transformers"
    tools:
      - name: "kubectl_multi"
        description: "Tool with multiple transformers"
        command: "kubectl get pods -o yaml"
        transformers:
          - name: llm_summarize
            config:
              input_threshold: 1000
              prompt: "First transformer"
          - name: llm_summarize
            config:
              input_threshold: 500
              prompt: "Second transformer"
"""

        # Write to temporary file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            # Load toolsets from the temporary file
            toolsets = load_toolsets_from_file(tmp_file_path)

            assert len(toolsets) == 1
            toolset = toolsets[0]
            tool = toolset.tools[0]

            assert tool.name == "kubectl_multi"
            assert tool.transformers is not None
            assert len(tool.transformers) == 2

            # Check first transformer
            config1 = tool.transformers[0].config
            assert config1["input_threshold"] == 1000
            assert config1["prompt"] == "First transformer"

            # Check second transformer
            config2 = tool.transformers[1].config
            assert config2["input_threshold"] == 500
            assert config2["prompt"] == "Second transformer"

        finally:
            # Clean up temporary file
            os.unlink(tmp_file_path)


class TestKubernetesTransformerPrompts:
    """Test the specific transformer prompts configured for Kubernetes tools."""

    def test_kubectl_logs_prompt_content(self):
        """Test that kubectl_logs has appropriate summarization prompt."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        kubernetes_logs_yaml_path = os.path.join(
            current_dir,
            "..",
            "..",
            "..",
            "holmes",
            "plugins",
            "toolsets",
            "kubernetes_logs.yaml",
        )

        toolsets = load_toolsets_from_file(kubernetes_logs_yaml_path)
        kubernetes_logs = next(
            (ts for ts in toolsets if ts.name == "kubernetes/logs"), None
        )
        assert kubernetes_logs is not None, "kubernetes/logs toolset not found"
        kubectl_logs = next(
            (tool for tool in kubernetes_logs.tools if tool.name == "kubectl_logs"),
            None,
        )
        assert kubectl_logs is not None, "kubectl_logs tool not found"

        assert kubectl_logs.transformers is not None
        prompt = kubectl_logs.transformers[0].config["prompt"]

        # Check that prompt contains key elements for log analysis
        assert "Errors, exceptions, and warning messages" in prompt
        assert "activity patterns" in prompt
        assert "authentication, connection" in prompt
        assert "Performance indicators" in prompt
        assert "exact error codes" in prompt

    def test_threshold_values_are_appropriate(self):
        """Test that threshold values are set appropriately for different tool types."""
        # Load both YAML files
        current_dir = os.path.dirname(os.path.abspath(__file__))

        # Test kubernetes core tools
        kubernetes_yaml_path = os.path.join(
            current_dir,
            "..",
            "..",
            "..",
            "holmes",
            "plugins",
            "toolsets",
            "kubernetes.yaml",
        )
        toolsets = load_toolsets_from_file(kubernetes_yaml_path)
        kubernetes_core = next(
            (ts for ts in toolsets if ts.name == "kubernetes/core"), None
        )
        assert kubernetes_core is not None, "kubernetes/core toolset not found"

        # kubernetes_jq_query should have threshold of 10000 (handles large JSON output)
        kubernetes_jq_query = next(
            (
                tool
                for tool in kubernetes_core.tools
                if tool.name == "kubernetes_jq_query"
            ),
            None,
        )
        assert (
            kubernetes_jq_query is not None
        ), "kubernetes_jq_query tool not found"
        assert kubernetes_jq_query.transformers is not None
        assert kubernetes_jq_query.transformers[0].config["input_threshold"] == 10000

        # Test kubernetes logs tools (higher threshold for logs)
        kubernetes_logs_yaml_path = os.path.join(
            current_dir,
            "..",
            "..",
            "..",
            "holmes",
            "plugins",
            "toolsets",
            "kubernetes_logs.yaml",
        )
        toolsets = load_toolsets_from_file(kubernetes_logs_yaml_path)
        kubernetes_logs = next(
            (ts for ts in toolsets if ts.name == "kubernetes/logs"), None
        )
        assert kubernetes_logs is not None, "kubernetes/logs toolset not found"

        # kubectl logs should have higher threshold of 1000 (logs can be longer)
        kubectl_logs = next(
            (tool for tool in kubernetes_logs.tools if tool.name == "kubectl_logs"),
            None,
        )
        assert kubectl_logs is not None, "kubectl_logs tool not found"
        assert kubectl_logs.transformers is not None
        assert kubectl_logs.transformers[0].config["input_threshold"] == 1000
