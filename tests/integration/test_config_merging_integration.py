"""
Integration tests for config merging functionality.
Tests the complete CLI --fast-model workflow with real-world toolset configurations.
"""

from unittest.mock import patch

from holmes.core.tools import ToolsetTag, YAMLTool, YAMLToolset
from holmes.core.toolset_manager import ToolsetManager
from holmes.core.transformers import Transformer


def create_kubernetes_toolset():
    """Create a Kubernetes toolset similar to the real one with transformers."""
    # Create tools with transformers like the real Kubernetes toolset
    kubectl_describe = YAMLTool(
        name="kubectl_describe",
        description="Run kubectl describe",
        command="kubectl describe {{ kind }} {{ name }}",
        transformers=[
            Transformer(
                name="llm_summarize",
                config={
                    "input_threshold": 1000,
                    "prompt": "Summarize kubectl describe output...",
                },
            )
        ],
    )

    kubectl_get = YAMLTool(
        name="kubectl_get_by_kind_in_namespace",
        description="Run kubectl get",
        command="kubectl get {{ kind }} -n {{ namespace }}",
        transformers=[
            Transformer(
                name="llm_summarize",
                config={
                    "input_threshold": 1000,
                    "prompt": "Summarize kubectl output...",
                },
            )
        ],
    )

    # Create toolset without fast_model (like real YAML configs)
    return YAMLToolset(
        name="kubernetes/core",
        tags=[ToolsetTag.CORE],
        description="Kubernetes toolset",
        tools=[kubectl_describe, kubectl_get],
    )


def test_cli_fast_model_integration_with_kubernetes():
    """
    Integration test: CLI --fast-model should work with Kubernetes toolset.
    This is the critical test case from the plan.
    """
    # Simulate CLI --fast-model setting
    global_fast_model = "azure/gpt-4.1"

    kubernetes_toolset = create_kubernetes_toolset()

    with patch("holmes.core.toolset_manager.load_builtin_toolsets") as mock_load:
        mock_load.return_value = [kubernetes_toolset]

        manager = ToolsetManager(global_fast_model=global_fast_model)
        toolsets = manager._list_all_toolsets(check_prerequisites=False)

        # Find the Kubernetes toolset
        k8s_toolset = next(t for t in toolsets if t.name == "kubernetes/core")

        # Verify that tools inherited the fast_model from global config
        kubectl_describe = next(
            t for t in k8s_toolset.tools if t.name == "kubectl_describe"
        )

        # Extract transformer config
        config_dict = {t.name: t.config for t in kubectl_describe.transformers}

        # Verify injection worked correctly
        assert (
            config_dict["llm_summarize"]["global_fast_model"] == "azure/gpt-4.1"
        )  # From CLI
        assert config_dict["llm_summarize"]["input_threshold"] == 1000  # From YAML
        assert "prompt" in config_dict["llm_summarize"]  # From YAML


def test_fast_model_injection_chain():
    """
    Test the fast model injection: global_fast_model → llm_summarize transformers
    """
    # Global fast model (CLI --fast-model)
    global_fast_model = "gpt-4.1"

    # Tool with specific transformer (no fast_model - should get injection)
    tool_with_transformer = YAMLTool(
        name="specific_tool",
        description="Tool with transformer",
        command="echo test",
        transformers=[
            Transformer(name="llm_summarize", config={"input_threshold": 2000})
        ],
    )

    # Tool without transformers (should inherit from toolset)
    tool_without_transformer = YAMLTool(
        name="generic_tool", description="Generic tool", command="echo generic"
    )

    # Toolset with transformers (should get injection)
    toolset = YAMLToolset(
        name="test_toolset",
        tags=[ToolsetTag.CORE],
        description="Test toolset",
        transformers=[
            Transformer(
                name="llm_summarize",
                config={"input_threshold": 1000, "prompt": "Toolset prompt"},
            )
        ],
        tools=[tool_with_transformer, tool_without_transformer],
    )

    with patch("holmes.core.toolset_manager.load_builtin_toolsets") as mock_load:
        mock_load.return_value = [toolset]

        manager = ToolsetManager(global_fast_model=global_fast_model)
        toolsets = manager._list_all_toolsets(check_prerequisites=False)

        test_toolset = toolsets[0]

        # Check toolset transformer (should get global_fast_model injection)
        assert test_toolset.transformers is not None
        toolset_config = {t.name: t.config for t in test_toolset.transformers}
        assert toolset_config["llm_summarize"]["global_fast_model"] == "gpt-4.1"
        assert toolset_config["llm_summarize"]["input_threshold"] == 1000  # Original
        assert "Toolset prompt" in toolset_config["llm_summarize"]["prompt"]

        # Check tool with transformer (should get global_fast_model injection)
        specific_tool = next(t for t in test_toolset.tools if t.name == "specific_tool")
        assert specific_tool.transformers is not None
        specific_config = {t.name: t.config for t in specific_tool.transformers}
        assert specific_config["llm_summarize"]["global_fast_model"] == "gpt-4.1"
        assert specific_config["llm_summarize"]["input_threshold"] == 2000  # Original

        # Check tool without transformers (should inherit from toolset and get injection)
        generic_tool = next(t for t in test_toolset.tools if t.name == "generic_tool")
        assert generic_tool.transformers is not None
        generic_config = {t.name: t.config for t in generic_tool.transformers}
        assert generic_config["llm_summarize"]["global_fast_model"] == "gpt-4.1"
        assert (
            generic_config["llm_summarize"]["input_threshold"] == 1000
        )  # From toolset
        assert (
            "Toolset prompt" in generic_config["llm_summarize"]["prompt"]
        )  # From toolset


def test_fast_model_injection_with_different_transformers():
    """
    Test that fast model injection works correctly with different transformer configurations.
    """
    global_fast_model = "gpt-4o-mini"

    # Transformer that should get injection
    toolset_configs = [
        Transformer(
            name="llm_summarize",
            config={"input_threshold": 1000, "prompt": "Toolset prompt"},
        )
    ]

    # Tool transformer that should get injection
    tool_configs = [
        Transformer(name="llm_summarize", config={"prompt": "Custom prompt"})
    ]

    tool = YAMLTool(
        name="multi_transformer_tool",
        description="Tool with transformer",
        command="echo test",
        transformers=tool_configs,
    )

    toolset = YAMLToolset(
        name="multi_transformer_toolset",
        tags=[ToolsetTag.CORE],
        description="Multi transformer toolset",
        transformers=toolset_configs,
        tools=[tool],
    )

    with patch("holmes.core.toolset_manager.load_builtin_toolsets") as mock_load:
        mock_load.return_value = [toolset]

        manager = ToolsetManager(global_fast_model=global_fast_model)
        toolsets = manager._list_all_toolsets(check_prerequisites=False)

        result_toolset = toolsets[0]
        result_tool = result_toolset.tools[0]

        # Check toolset transformer got injection
        assert result_toolset.transformers is not None
        toolset_config = {t.name: t.config for t in result_toolset.transformers}
        assert toolset_config["llm_summarize"]["global_fast_model"] == "gpt-4o-mini"
        assert toolset_config["llm_summarize"]["input_threshold"] == 1000  # Original
        assert "Toolset prompt" in toolset_config["llm_summarize"]["prompt"]

        # Check tool transformer got injection
        assert result_tool.transformers is not None
        final_config = {t.name: t.config for t in result_tool.transformers}
        assert final_config["llm_summarize"]["global_fast_model"] == "gpt-4o-mini"
        assert "Custom prompt" in final_config["llm_summarize"]["prompt"]

        # Should have 1 transformer type
        assert len(final_config) == 1


def test_backward_compatibility():
    """
    Test that toolsets without transformers still work correctly (no injection occurs).
    """
    global_fast_model = "gpt-4o-mini"

    # Toolset without transformers (like some existing toolsets)
    simple_toolset = YAMLToolset(
        name="simple_toolset",
        tags=[ToolsetTag.CORE],
        description="Simple toolset without transformers",
        tools=[YAMLTool(name="simple_tool", description="Simple", command="echo")],
    )

    with patch("holmes.core.toolset_manager.load_builtin_toolsets") as mock_load:
        mock_load.return_value = [simple_toolset]

        manager = ToolsetManager(global_fast_model=global_fast_model)
        toolsets = manager._list_all_toolsets(check_prerequisites=False)

        result_toolset = toolsets[0]

        # Toolset should NOT receive any transformers (new injection behavior)
        assert result_toolset.transformers is None

        # Tool should also remain without transformers
        tool = result_toolset.tools[0]
        assert tool.transformers is None


def test_no_global_configs_no_regression():
    """
    Test that existing behavior is unchanged when no global configs are provided.
    """
    toolset_configs = [
        Transformer(name="llm_summarize", config={"input_threshold": 1000})
    ]

    toolset = YAMLToolset(
        name="existing_toolset",
        tags=[ToolsetTag.CORE],
        description="Existing toolset",
        transformers=toolset_configs,
    )

    with patch("holmes.core.toolset_manager.load_builtin_toolsets") as mock_load:
        mock_load.return_value = [toolset]

        # No global configs (normal case)
        manager = ToolsetManager()
        toolsets = manager._list_all_toolsets(check_prerequisites=False)

        result_toolset = toolsets[0]

        # Should remain unchanged
        assert result_toolset.transformers == toolset_configs


def test_toolset_with_only_tool_level_transformers_gets_fast_model():
    """
    Test that toolsets with ONLY tool-level transformers (no toolset-level transformers)
    DO receive global fast-model settings with the new simplified injection approach.

    This test verifies the fix for the issue where kubernetes_jq_query didn't get fast_model.
    """
    # Global fast_model from CLI --fast-model
    global_fast_model = "gpt-4o-mini"

    # Create a tool with transformers (like kubernetes_jq_query)
    jq_query_tool = YAMLTool(
        name="kubernetes_jq_query",
        description="Query Kubernetes Resources with jq",
        command="kubectl get {{ kind }} --all-namespaces -o json | jq -r {{ jq_expr }}",
        transformers=[
            Transformer(
                name="llm_summarize",
                config={
                    "input_threshold": 1000,
                    "prompt": "Summarize jq query output focusing on patterns...",
                },
            )
        ],
    )

    # Create toolset WITHOUT toolset-level transformers (like many real toolsets)
    toolset_without_toolset_transformers = YAMLToolset(
        name="kubernetes/core",
        tags=[ToolsetTag.CORE],
        description="Kubernetes toolset with only tool-level transformers",
        tools=[jq_query_tool],
        # Note: NO transformers defined at toolset level
    )

    with patch("holmes.core.toolset_manager.load_builtin_toolsets") as mock_load:
        mock_load.return_value = [toolset_without_toolset_transformers]

        manager = ToolsetManager(global_fast_model=global_fast_model)
        toolsets = manager._list_all_toolsets(check_prerequisites=False)

        result_toolset = toolsets[0]

        # The tool SHOULD now receive the global fast_model via injection
        jq_tool = next(
            t for t in result_toolset.tools if t.name == "kubernetes_jq_query"
        )
        config_dict = {t.name: t.config for t in jq_tool.transformers}

        # This assertion should now PASS, proving the issue is fixed
        assert "global_fast_model" in config_dict["llm_summarize"], (
            "Tool should have global_fast_model because new injection mechanism "
            "injects it into all tools regardless of toolset-level transformers"
        )
        assert config_dict["llm_summarize"]["global_fast_model"] == "gpt-4o-mini"

        # Tool should still have its original config
        assert config_dict["llm_summarize"]["input_threshold"] == 1000
        assert "Summarize jq query output" in config_dict["llm_summarize"]["prompt"]


def test_toolset_with_toolset_level_transformers_works():
    """
    Contrast test: Verify that toolsets WITH toolset-level transformers
    DO receive global fast-model injection correctly.
    """
    # Global config from CLI --fast-model
    global_fast_model = "gpt-4o-mini"

    # Create a tool with transformers
    kubectl_describe = YAMLTool(
        name="kubectl_describe",
        description="Run kubectl describe",
        command="kubectl describe {{ kind }} {{ name }}",
        transformers=[
            Transformer(
                name="llm_summarize",
                config={
                    "input_threshold": 1000,
                    "prompt": "Summarize kubectl describe output...",
                },
            )
        ],
    )

    # Create toolset WITH toolset-level transformers
    toolset_with_toolset_transformers = YAMLToolset(
        name="kubernetes/core",
        tags=[ToolsetTag.CORE],
        description="Kubernetes toolset with toolset-level transformers",
        tools=[kubectl_describe],
        # KEY DIFFERENCE: Has toolset-level transformers
        transformers=[
            Transformer(
                name="llm_summarize",
                config={"input_threshold": 800},  # Different from tool-level
            )
        ],
    )

    with patch("holmes.core.toolset_manager.load_builtin_toolsets") as mock_load:
        mock_load.return_value = [toolset_with_toolset_transformers]

        manager = ToolsetManager(global_fast_model=global_fast_model)
        toolsets = manager._list_all_toolsets(check_prerequisites=False)

        result_toolset = toolsets[0]

        # Toolset SHOULD receive global_fast_model injection
        assert result_toolset.transformers is not None
        toolset_config = {t.name: t.config for t in result_toolset.transformers}
        assert toolset_config["llm_summarize"]["global_fast_model"] == "gpt-4o-mini"

        # Tool SHOULD also receive global_fast_model injection
        describe_tool = next(
            t for t in result_toolset.tools if t.name == "kubectl_describe"
        )
        tool_config = {t.name: t.config for t in describe_tool.transformers}

        # Should have global_fast_model injected
        assert tool_config["llm_summarize"]["global_fast_model"] == "gpt-4o-mini"
        # Should have input_threshold from tool (original)
        assert tool_config["llm_summarize"]["input_threshold"] == 1000
        # Should have prompt from tool
        assert (
            "Summarize kubectl describe output"
            in tool_config["llm_summarize"]["prompt"]
        )
