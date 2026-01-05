import logging
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from holmes.config import Config
from holmes.core.llm import LLMModelRegistry, ModelEntry


class TestLLMModelRegistryGetModelParams:
    """Test LLMModelRegistry.get_model_params method."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config for testing."""
        config = MagicMock(spec=Config)
        config.should_try_robusta_ai = False
        config.model = None
        config.cluster_name = None
        return config

    @pytest.fixture
    def mock_dal(self):
        """Create a mock DAL for testing."""
        dal = MagicMock()
        dal.enabled = False
        dal.account_id = None
        return dal

    @pytest.fixture
    def gpt4o(self):
        return ModelEntry(
            model="gpt-4o",
            name="gpt4o",
            api_key=SecretStr("test-key"),
        )

    @pytest.fixture
    def gpt5(self):
        return ModelEntry(
            model="gpt-5o",
            name="gpt5",
            api_key=SecretStr("test-key"),
        )

    def test_get_model_params_with_valid_model_key(
        self, mock_config, mock_dal, gpt4o, monkeypatch
    ):
        """Test get_model_params returns model when model_key exists."""

        monkeypatch.setattr(
            "holmes.core.llm.LLMModelRegistry._parse_models_file",
            lambda self, path: {"gpt4o": gpt4o},
        )
        registry = LLMModelRegistry(mock_config, mock_dal)
        model_params = registry.get_model_params("gpt4o")

        assert model_params.model == "gpt-4o"
        assert model_params.name == "gpt4o"

    def test_get_model_params_with_invalid_model_key_returns_first(
        self, mock_config, mock_dal, monkeypatch, gpt4o, gpt5
    ):
        """
        Test get_model_params returns first model when key not found.
        """
        monkeypatch.setattr(
            "holmes.core.llm.LLMModelRegistry._parse_models_file",
            lambda self, path: {"gpt5": gpt5, "gpt4o": gpt4o},
        )
        registry = LLMModelRegistry(mock_config, mock_dal)
        model_params = registry.get_model_params("test-model")

        assert model_params.model == "gpt-5o"
        assert model_params.name == "gpt5"

    def test_get_model_params_with_default_robusta_model(
        self, mock_config, mock_dal, gpt4o, gpt5, monkeypatch
    ):
        """
        Test get_model_params returns default Robusta model.
        """
        monkeypatch.setattr(
            "holmes.core.llm.LLMModelRegistry._parse_models_file",
            lambda self, path: {"gpt5": gpt5, "gpt4o": gpt4o},
        )
        registry = LLMModelRegistry(mock_config, mock_dal)
        registry._default_robusta_model = "gpt4o"
        model_params = registry.get_model_params("test-model")

        assert model_params.model == "gpt-4o"
        assert model_params.name == "gpt4o"

    def test_get_model_params_robusta_resync_behavior(
        self, mock_config, mock_dal, monkeypatch, gpt4o, gpt5, caplog
    ):
        """
        Test get_model_params resyncs when Robusta model not found.
        """
        # Setup initial models without the requested Robusta model
        monkeypatch.setattr(
            "holmes.core.llm.LLMModelRegistry._parse_models_file",
            lambda self, path: {"gpt5": gpt5, "gpt4o": gpt4o},
        )
        registry = LLMModelRegistry(mock_config, mock_dal)
        monkeypatch.setattr(
            "holmes.core.llm.LLMModelRegistry._parse_models_file",
            lambda self, path: {
                "Robusta/test": ModelEntry(
                    model="sonnet-4",
                    name="Robusta/test",
                    api_key=SecretStr("test-key"),
                ),
                "gpt4o": gpt4o,
            },
        )
        model_params = registry.get_model_params("Robusta/test")

        assert model_params.model == "sonnet-4"
        assert model_params.name == "Robusta/test"

    def test_get_model_params_robusta_resync_still_not_found(
        self, mock_config, mock_dal, caplog, monkeypatch, gpt5, gpt4o
    ):
        """
        Test get_model_params when Robusta model not found after resync.
        """
        monkeypatch.setattr(
            "holmes.core.llm.LLMModelRegistry._parse_models_file",
            lambda self, path: {"gpt5": gpt5, "gpt4o": gpt4o},
        )
        registry = LLMModelRegistry(mock_config, mock_dal)
        with caplog.at_level(logging.WARNING):
            model_params = registry.get_model_params("Robusta/non-existent")

        assert "Resyncing Registry and Robusta models" in caplog.text
        error_msg = "Couldn't find model: Robusta/non-existent in model list"
        assert error_msg in caplog.text

        assert model_params.model == "gpt-5o"
        assert model_params.name == "gpt5"
