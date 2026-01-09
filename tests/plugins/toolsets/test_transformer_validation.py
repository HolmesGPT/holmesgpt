"""
Tests for transformer validation and conversion logic introduced with Pydantic refactoring.
"""

import os
import tempfile
from unittest.mock import patch

from holmes.plugins.toolsets import load_toolsets_from_file

from .transformer_test_utils import ensure_transformers_registered


class TestTransformerValidationAndConversion:
    """Test the new transformer validation and conversion logic in preprocess_tools."""

    def test_malformed_transformer_dict_tool_level(self):
        """Test that malformed transformer dicts at tool level are handled gracefully."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        yaml_content = """
toolsets:
  test/malformed:
    description: "Test toolset with malformed transformer config at tool level"
    tools:
      - name: "kubectl_malformed"
        description: "Tool with malformed transformers"
        command: "kubectl get pods"
        transformers:
          - name: llm_summarize
            config:
              input_threshold: 1000
          - invalid_structure: "missing required fields"
          - name: "missing_config_field"
"""

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            # Focus on the core functionality - invalid transformers should be filtered out
            toolsets = load_toolsets_from_file(tmp_file_path)

            assert len(toolsets) == 1
            toolset = toolsets[0]

            # Tool should have only the valid transformer
            tool = toolset.tools[0]
            assert tool.name == "kubectl_malformed"
            assert tool.transformers is not None
            assert len(tool.transformers) == 1
            assert tool.transformers[0].name == "llm_summarize"

        finally:
            os.unlink(tmp_file_path)

    def test_mixed_valid_invalid_transformers_toolset_level(self):
        """Test mixing valid and invalid transformers at toolset level."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        yaml_content = """
toolsets:
  test/mixed:
    description: "Test toolset with mixed valid/invalid transformers"
    transformers:
      - name: llm_summarize
        config:
          input_threshold: 500
      - name: unknown_transformer
        config:
          param: "value"
      - name: llm_summarize
        config:
          input_threshold: 1000
          prompt: "Second valid transformer"
    tools:
      - name: "kubectl_inherit"
        description: "Tool that inherits mixed transformers"
        command: "kubectl get pods"
"""

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            with patch("holmes.core.tools.logging") as mock_tools_logging, patch(
                "holmes.core.transformers.transformer.logging"
            ) as mock_transformer_logging:
                toolsets = load_toolsets_from_file(tmp_file_path)

                assert len(toolsets) == 1
                toolset = toolsets[0]

                # Tool should inherit only valid transformers
                tool = toolset.tools[0]
                assert tool.transformers is not None
                # Should have 2 valid llm_summarize transformers
                assert len(tool.transformers) == 2
                assert all(t.name == "llm_summarize" for t in tool.transformers)

                # Should have logged warning for unknown_transformer in either module
                assert (
                    mock_tools_logging.warning.called
                    or mock_transformer_logging.warning.called
                )

        finally:
            os.unlink(tmp_file_path)

    def test_mixed_valid_invalid_transformers_tool_level(self):
        """Test mixing valid and invalid transformers at tool level."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        yaml_content = """
toolsets:
  test/mixed_tool:
    description: "Test toolset with mixed transformers at tool level"
    tools:
      - name: "kubectl_mixed"
        description: "Tool with mixed valid/invalid transformers"
        command: "kubectl get pods"
        transformers:
          - name: llm_summarize
            config:
              input_threshold: 800
          - name: invalid_transformer
            config:
              param: "value"
          - malformed: "structure"
"""

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            # Don't worry about exact logging counts, just test the core functionality
            toolsets = load_toolsets_from_file(tmp_file_path)

            assert len(toolsets) == 1
            toolset = toolsets[0]

            # Tool should have only valid transformers
            tool = toolset.tools[0]
            assert tool.transformers is not None
            assert len(tool.transformers) == 1
            assert tool.transformers[0].name == "llm_summarize"
            assert tool.transformers[0].config["input_threshold"] == 800

        finally:
            os.unlink(tmp_file_path)

    def test_inheritance_with_mixed_validation_states(self):
        """Test inheritance when toolset has valid transformers and tool has invalid ones."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        yaml_content = """
toolsets:
  test/inheritance_mixed:
    description: "Test inheritance with mixed validation states"
    transformers:
      - name: llm_summarize
        config:
          input_threshold: 500
          prompt: "Toolset default"
    tools:
      - name: "kubectl_inherit_good"
        description: "Tool that inherits valid toolset transformers"
        command: "kubectl get pods"

      - name: "kubectl_override_bad"
        description: "Tool with invalid transformers that should fall back to toolset"
        command: "kubectl describe pod"
        transformers:
          - name: unknown_transformer
            config:
              param: "value"
          - malformed: "config"

      - name: "kubectl_override_mixed"
        description: "Tool with mixed transformers that should merge with toolset"
        command: "kubectl logs pod"
        transformers:
          - name: llm_summarize
            config:
              input_threshold: 1200
              prompt: "Tool override"
          - name: invalid_transformer
            config:
              param: "value"
"""

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            with patch("holmes.core.tools.logging") as mock_tools_logging, patch(
                "holmes.core.transformers.transformer.logging"
            ) as mock_transformer_logging:
                toolsets = load_toolsets_from_file(tmp_file_path)

                assert len(toolsets) == 1
                toolset = toolsets[0]
                assert len(toolset.tools) == 3

                # Tool 1: Should inherit valid toolset transformers
                inherit_tool = toolset.tools[0]
                assert inherit_tool.name == "kubectl_inherit_good"
                assert inherit_tool.transformers is not None
                assert len(inherit_tool.transformers) == 1
                assert (
                    inherit_tool.transformers[0].config["prompt"] == "Toolset default"
                )

                # Tool 2: Should fall back to toolset transformers after invalid tool transformers are filtered
                fallback_tool = toolset.tools[1]
                assert fallback_tool.name == "kubectl_override_bad"
                assert fallback_tool.transformers is not None
                assert len(fallback_tool.transformers) == 1
                assert (
                    fallback_tool.transformers[0].config["prompt"] == "Toolset default"
                )

                # Tool 3: Should have merged valid transformers (tool overrides toolset)
                mixed_tool = toolset.tools[2]
                assert mixed_tool.name == "kubectl_override_mixed"
                assert mixed_tool.transformers is not None
                assert len(mixed_tool.transformers) == 1
                assert mixed_tool.transformers[0].config["input_threshold"] == 1200
                assert mixed_tool.transformers[0].config["prompt"] == "Tool override"

                # Should have logged warnings for invalid transformers in either module
                assert (
                    mock_tools_logging.warning.called
                    or mock_transformer_logging.warning.called
                )

        finally:
            os.unlink(tmp_file_path)

    def test_empty_transformer_lists_after_validation(self):
        """Test behavior when all transformers are invalid and lists become empty."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        yaml_content = """
toolsets:
  test/all_invalid:
    description: "Test toolset where all transformers are invalid"
    transformers:
      - name: unknown_transformer_1
        config:
          param: "value1"
      - name: unknown_transformer_2
        config:
          param: "value2"
    tools:
      - name: "kubectl_no_transformers"
        description: "Tool that should end up with no transformers"
        command: "kubectl get pods"
"""

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            with patch("holmes.core.tools.logging") as mock_tools_logging, patch(
                "holmes.core.transformers.transformer.logging"
            ) as mock_transformer_logging:
                toolsets = load_toolsets_from_file(tmp_file_path)

                assert len(toolsets) == 1
                toolset = toolsets[0]

                # Tool should have no transformers after invalid ones are filtered
                tool = toolset.tools[0]
                assert tool.transformers is None

                # Should have logged warnings for both invalid transformers in either module
                total_warning_calls = (
                    mock_tools_logging.warning.call_count
                    + mock_transformer_logging.warning.call_count
                )
                assert total_warning_calls >= 2

        finally:
            os.unlink(tmp_file_path)

    def test_transformer_conversion_preserves_config_structure(self):
        """Test that the dict-to-Transformer conversion preserves complex config structures."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        yaml_content = """
toolsets:
  test/complex_config:
    description: "Test toolset with complex transformer configurations"
    transformers:
      - name: llm_summarize
        config:
          input_threshold: 1000
          prompt: |
            Multi-line prompt with:
            - Bullet points
            - Complex formatting
            - Variables: {{ variable }}
          nested_config:
            level1:
              level2: "deep value"
            array: [1, 2, 3]
          boolean_flag: true
          null_value: null
    tools:
      - name: "kubectl_complex"
        description: "Tool with complex config"
        command: "kubectl get pods"
"""

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        try:
            toolsets = load_toolsets_from_file(tmp_file_path)

            assert len(toolsets) == 1
            toolset = toolsets[0]
            tool = toolset.tools[0]

            assert tool.transformers is not None
            assert len(tool.transformers) == 1

            config = tool.transformers[0].config
            assert config["input_threshold"] == 1000
            assert "Multi-line prompt with:" in config["prompt"]
            assert config["nested_config"]["level1"]["level2"] == "deep value"
            assert config["nested_config"]["array"] == [1, 2, 3]
            assert config["boolean_flag"] is True
            assert config["null_value"] is None

        finally:
            os.unlink(tmp_file_path)
