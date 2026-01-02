"""
Unit tests for Config.fast_model flow to ToolsetManager.

These tests verify that the fast_model configuration flows correctly from
Config.fast_model to ToolsetManager.global_fast_model.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from holmes.config import Config


class TestConfigFastModelFlow:
    """Test that Config.fast_model flows correctly to ToolsetManager."""

    def test_config_fast_model_flows_to_toolset_manager(self):
        """Test that Config.fast_model is passed to ToolsetManager.global_fast_model."""
        # Create config with fast_model
        config = Config(fast_model="gpt-3.5-turbo")

        # Verify fast_model is set
        assert config.fast_model == "gpt-3.5-turbo"

        # Verify ToolsetManager receives the fast_model
        toolset_manager = config.toolset_manager
        assert toolset_manager.global_fast_model == "gpt-3.5-turbo"

    def test_config_no_fast_model_flows_to_toolset_manager(self):
        """Test that Config without fast_model passes None to ToolsetManager."""
        # Create config without fast_model
        config = Config()

        # Verify fast_model is None
        assert config.fast_model is None

        # Verify ToolsetManager receives None
        toolset_manager = config.toolset_manager
        assert toolset_manager.global_fast_model is None

    def test_config_from_file_fast_model_flows_to_toolset_manager(self):
        """Test that Config loaded from file passes fast_model to ToolsetManager."""
        # Create temporary config file with fast_model
        config_data = {"model": "gpt-4o", "fast_model": "gpt-4o-mini", "max_steps": 20}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = Path(f.name)

        try:
            # Mock dependencies to avoid side effects
            with patch(
                "holmes.core.llm.LLMModelRegistry._parse_models_file", return_value={}
            ):
                config = Config.load_from_file(config_path)

            # Verify fast_model is loaded
            assert config.fast_model == "gpt-4o-mini"

            # Verify ToolsetManager receives the fast_model
            toolset_manager = config.toolset_manager
            assert toolset_manager.global_fast_model == "gpt-4o-mini"

        finally:
            config_path.unlink()  # Clean up temp file

    def test_config_from_env_fast_model_flows_to_toolset_manager(self):
        """Test that Config loaded from env passes fast_model to ToolsetManager."""
        test_env = {"MODEL": "gpt-4o", "FAST_MODEL": "gpt-3.5-turbo"}

        with patch.dict("os.environ", test_env):
            with patch(
                "holmes.config.Config._Config__get_cluster_name", return_value=None
            ):
                with patch(
                    "holmes.core.llm.LLMModelRegistry._parse_models_file",
                    return_value={},
                ):
                    config = Config.load_from_env()

        # Verify fast_model is loaded from env
        assert config.fast_model == "gpt-3.5-turbo"

        # Verify ToolsetManager receives the fast_model
        toolset_manager = config.toolset_manager
        assert toolset_manager.global_fast_model == "gpt-3.5-turbo"

    def test_config_cli_override_fast_model_flows_to_toolset_manager(self):
        """Test that CLI override of fast_model flows to ToolsetManager."""
        # Create temporary config file without fast_model
        config_data = {"model": "gpt-4o", "max_steps": 20}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = Path(f.name)

        try:
            # Mock dependencies to avoid side effects
            with patch(
                "holmes.core.llm.LLMModelRegistry._parse_models_file", return_value={}
            ):
                # CLI option overrides file config
                config = Config.load_from_file(
                    config_path, fast_model="claude-3-sonnet"
                )

            # Verify fast_model is set from CLI
            assert config.fast_model == "claude-3-sonnet"

            # Verify ToolsetManager receives the CLI fast_model
            toolset_manager = config.toolset_manager
            assert toolset_manager.global_fast_model == "claude-3-sonnet"

        finally:
            config_path.unlink()  # Clean up temp file

    def test_config_toolset_manager_caching(self):
        """Test that toolset_manager property is cached correctly."""
        config = Config(fast_model="gpt-4o-mini")

        # First access creates the toolset manager
        toolset_manager1 = config.toolset_manager
        assert toolset_manager1.global_fast_model == "gpt-4o-mini"

        # Second access returns the same instance
        toolset_manager2 = config.toolset_manager
        assert toolset_manager2 is toolset_manager1
        assert toolset_manager2.global_fast_model == "gpt-4o-mini"
