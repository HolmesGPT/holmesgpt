"""
Unit tests for Config.fast_model flow to LLMSummarizeTransformer.

These tests verify that the fast_model configuration flows correctly from
Config.fast_model to LLMSummarizeTransformer._default_fast_model via the
class-level setter, without going through ToolsetManager.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from holmes.config import Config
from holmes.core.transformers.llm_summarize import LLMSummarizeTransformer


class TestConfigFastModelFlow:
    """Test that Config.fast_model flows correctly to LLMSummarizeTransformer."""

    def setup_method(self):
        """Save original class-level default so we can restore it."""
        self._original_default = LLMSummarizeTransformer._default_fast_model

    def teardown_method(self):
        """Restore class-level default after each test."""
        LLMSummarizeTransformer._default_fast_model = self._original_default

    def test_config_fast_model_sets_transformer_default(self):
        """Test that Config.fast_model sets the class-level default on LLMSummarizeTransformer."""
        config = Config(fast_model="gpt-3.5-turbo")
        assert config.fast_model == "gpt-3.5-turbo"

        # Accessing toolset_manager triggers set_default_fast_model
        _ = config.toolset_manager
        assert LLMSummarizeTransformer._default_fast_model == "gpt-3.5-turbo"

    def test_config_no_fast_model_leaves_transformer_default(self):
        """Test that Config without fast_model does not change the transformer default."""
        LLMSummarizeTransformer._default_fast_model = None
        config = Config()
        assert config.fast_model is None

        _ = config.toolset_manager
        assert LLMSummarizeTransformer._default_fast_model is None

    def test_config_from_file_fast_model_sets_transformer_default(self):
        """Test that Config loaded from file sets the transformer default."""
        config_data = {"model": "gpt-4o", "fast_model": "gpt-4o-mini", "max_steps": 20}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = Path(f.name)

        try:
            with patch(
                "holmes.core.llm.LLMModelRegistry._parse_models_file", return_value={}
            ):
                config = Config.load_from_file(config_path)

            assert config.fast_model == "gpt-4o-mini"

            _ = config.toolset_manager
            assert LLMSummarizeTransformer._default_fast_model == "gpt-4o-mini"

        finally:
            config_path.unlink()

    def test_config_from_env_fast_model_sets_transformer_default(self):
        """Test that Config loaded from env sets the transformer default."""
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

        assert config.fast_model == "gpt-3.5-turbo"

        _ = config.toolset_manager
        assert LLMSummarizeTransformer._default_fast_model == "gpt-3.5-turbo"

    def test_config_cli_override_fast_model_sets_transformer_default(self):
        """Test that CLI override of fast_model sets the transformer default."""
        config_data = {"model": "gpt-4o", "max_steps": 20}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = Path(f.name)

        try:
            with patch(
                "holmes.core.llm.LLMModelRegistry._parse_models_file", return_value={}
            ):
                config = Config.load_from_file(
                    config_path, fast_model="claude-3-sonnet"
                )

            assert config.fast_model == "claude-3-sonnet"

            _ = config.toolset_manager
            assert LLMSummarizeTransformer._default_fast_model == "claude-3-sonnet"

        finally:
            config_path.unlink()

    def test_config_toolset_manager_caching(self):
        """Test that toolset_manager property is cached correctly."""
        config = Config(fast_model="gpt-4o-mini")

        toolset_manager1 = config.toolset_manager
        toolset_manager2 = config.toolset_manager
        assert toolset_manager2 is toolset_manager1
        assert LLMSummarizeTransformer._default_fast_model == "gpt-4o-mini"
