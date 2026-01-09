"""
Unit tests for AKS toolsets with transformer configurations.
"""

import os

from holmes.plugins.toolsets import load_toolsets_from_file

from .transformer_test_utils import ensure_transformers_registered


class TestAKSTransformers:
    """Test that AKS toolsets correctly parse transformer configurations."""

    def test_load_aks_node_health_yaml_with_transformers(self):
        """Test loading the aks-node-health.yaml file with transformers."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        # Find the actual aks-node-health.yaml file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        aks_node_health_yaml_path = os.path.join(
            current_dir,
            "..",
            "..",
            "..",
            "holmes",
            "plugins",
            "toolsets",
            "aks-node-health.yaml",
        )

        # Load toolsets from the file
        toolsets = load_toolsets_from_file(aks_node_health_yaml_path)

        # Find the aks/node-health toolset
        aks_node_health = None
        for toolset in toolsets:
            if toolset.name == "aks/node-health":
                aks_node_health = toolset
                break

        assert aks_node_health is not None, "aks/node-health toolset not found"

        # Test check_node_status has transformer config
        check_node_status = None
        for tool in aks_node_health.tools:
            if tool.name == "check_node_status":
                check_node_status = tool
                break

        assert check_node_status is not None, "check_node_status tool not found"
        assert check_node_status.transformers is not None
        assert len(check_node_status.transformers) == 1
        assert check_node_status.transformers[0].name == "llm_summarize"
        assert check_node_status.transformers[0].config["input_threshold"] == 800

        # Test describe_node has transformer config
        describe_node = None
        for tool in aks_node_health.tools:
            if tool.name == "describe_node":
                describe_node = tool
                break

        assert describe_node is not None, "describe_node tool not found"
        assert describe_node.transformers is not None
        assert len(describe_node.transformers) == 1
        assert describe_node.transformers[0].name == "llm_summarize"
        assert describe_node.transformers[0].config["input_threshold"] == 1200

    def test_load_aks_core_yaml_with_transformers(self):
        """Test loading the aks.yaml file with transformers."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        # Find the actual aks.yaml file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        aks_yaml_path = os.path.join(
            current_dir, "..", "..", "..", "holmes", "plugins", "toolsets", "aks.yaml"
        )

        # Load toolsets from the file
        toolsets = load_toolsets_from_file(aks_yaml_path)

        # Find the aks/core toolset
        aks_core = None
        for toolset in toolsets:
            if toolset.name == "aks/core":
                aks_core = toolset
                break

        assert aks_core is not None, "aks/core toolset not found"

        # Test aks_get_cluster has transformer config
        aks_get_cluster = None
        for tool in aks_core.tools:
            if tool.name == "aks_get_cluster":
                aks_get_cluster = tool
                break

        assert aks_get_cluster is not None, "aks_get_cluster tool not found"
        assert aks_get_cluster.transformers is not None
        assert len(aks_get_cluster.transformers) == 1
        assert aks_get_cluster.transformers[0].name == "llm_summarize"
        assert aks_get_cluster.transformers[0].config["input_threshold"] == 1500

        # Test aks_list_node_pools has transformer config
        aks_list_node_pools = None
        for tool in aks_core.tools:
            if tool.name == "aks_list_node_pools":
                aks_list_node_pools = tool
                break

        assert aks_list_node_pools is not None, "aks_list_node_pools tool not found"
        assert aks_list_node_pools.transformers is not None
        assert len(aks_list_node_pools.transformers) == 1
        assert aks_list_node_pools.transformers[0].name == "llm_summarize"
        assert aks_list_node_pools.transformers[0].config["input_threshold"] == 1200

    def test_aks_transformer_prompts(self):
        """Test that AKS tools have appropriate transformer prompts."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        # Test AKS node health prompts
        current_dir = os.path.dirname(os.path.abspath(__file__))
        aks_node_health_yaml_path = os.path.join(
            current_dir,
            "..",
            "..",
            "..",
            "holmes",
            "plugins",
            "toolsets",
            "aks-node-health.yaml",
        )

        toolsets = load_toolsets_from_file(aks_node_health_yaml_path)
        aks_node_health = next(
            (ts for ts in toolsets if ts.name == "aks/node-health"), None
        )
        assert aks_node_health is not None, "aks/node-health toolset not found"

        # Test check_node_status prompt
        check_node_status = next(
            (
                tool
                for tool in aks_node_health.tools
                if tool.name == "check_node_status"
            ),
            None,
        )
        assert check_node_status is not None, "check_node_status tool not found"
        assert check_node_status.transformers is not None
        prompt = check_node_status.transformers[0].config["prompt"]
        assert "NotReady or in error states" in prompt
        assert "Node health patterns" in prompt
        assert "exact node names" in prompt

        # Test describe_node prompt
        describe_node = next(
            (tool for tool in aks_node_health.tools if tool.name == "describe_node"),
            None,
        )
        assert describe_node is not None, "describe_node tool not found"
        assert describe_node.transformers is not None
        prompt = describe_node.transformers[0].config["prompt"]
        assert "Node conditions and health status" in prompt
        assert "Resource capacity vs allocatable" in prompt
        assert "taints, labels" in prompt

        # Test AKS core prompts
        aks_yaml_path = os.path.join(
            current_dir, "..", "..", "..", "holmes", "plugins", "toolsets", "aks.yaml"
        )

        toolsets = load_toolsets_from_file(aks_yaml_path)
        aks_core = next((ts for ts in toolsets if ts.name == "aks/core"), None)
        assert aks_core is not None, "aks/core toolset not found"

        # Test aks_get_cluster prompt
        aks_get_cluster = next(
            (tool for tool in aks_core.tools if tool.name == "aks_get_cluster"), None
        )
        assert aks_get_cluster is not None, "aks_get_cluster tool not found"
        assert aks_get_cluster.transformers is not None
        prompt = aks_get_cluster.transformers[0].config["prompt"]
        assert "Cluster status, health state" in prompt
        assert "Network configuration" in prompt
        assert "Security settings" in prompt

    def test_aks_transformer_thresholds(self):
        """Test that AKS tools have appropriate transformer thresholds."""
        # Ensure transformer registry is properly initialized
        ensure_transformers_registered()

        # Load both YAML files
        current_dir = os.path.dirname(os.path.abspath(__file__))

        # Test AKS node health thresholds
        aks_node_health_yaml_path = os.path.join(
            current_dir,
            "..",
            "..",
            "..",
            "holmes",
            "plugins",
            "toolsets",
            "aks-node-health.yaml",
        )
        toolsets = load_toolsets_from_file(aks_node_health_yaml_path)
        aks_node_health = next(
            (ts for ts in toolsets if ts.name == "aks/node-health"), None
        )
        assert aks_node_health is not None, "aks/node-health toolset not found"

        # check_node_status should have lower threshold (simpler output)
        check_node_status = next(
            (
                tool
                for tool in aks_node_health.tools
                if tool.name == "check_node_status"
            ),
            None,
        )
        assert check_node_status is not None, "check_node_status tool not found"
        assert check_node_status.transformers is not None
        assert check_node_status.transformers[0].config["input_threshold"] == 800

        # describe_node should have higher threshold (detailed output)
        describe_node = next(
            (tool for tool in aks_node_health.tools if tool.name == "describe_node"),
            None,
        )
        assert describe_node is not None, "describe_node tool not found"
        assert describe_node.transformers is not None
        assert describe_node.transformers[0].config["input_threshold"] == 1200

        # Test AKS core thresholds
        aks_yaml_path = os.path.join(
            current_dir, "..", "..", "..", "holmes", "plugins", "toolsets", "aks.yaml"
        )
        toolsets = load_toolsets_from_file(aks_yaml_path)
        aks_core = next((ts for ts in toolsets if ts.name == "aks/core"), None)
        assert aks_core is not None, "aks/core toolset not found"

        # aks_get_cluster should have highest threshold (very detailed JSON)
        aks_get_cluster = next(
            (tool for tool in aks_core.tools if tool.name == "aks_get_cluster"), None
        )
        assert aks_get_cluster is not None, "aks_get_cluster tool not found"
        assert aks_get_cluster.transformers is not None
        assert aks_get_cluster.transformers[0].config["input_threshold"] == 1500

        # aks_list_clusters_by_rg should have medium threshold (list of clusters)
        aks_list_clusters = next(
            (tool for tool in aks_core.tools if tool.name == "aks_list_clusters_by_rg"),
            None,
        )
        assert aks_list_clusters is not None, "aks_list_clusters_by_rg tool not found"
        assert aks_list_clusters.transformers is not None
        assert aks_list_clusters.transformers[0].config["input_threshold"] == 1000
