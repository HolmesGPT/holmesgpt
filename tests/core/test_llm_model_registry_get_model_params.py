import logging
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from holmes.clients.robusta_client import RobustaModel, RobustaModelsResponse
from holmes.config import Config
from holmes.core.llm import LLMModelRegistry, ModelEntry


class TestLLMModelRegistryGetModelParams:
    """Test LLMModelRegistry.get_model_params method."""

    @pytest.fixture
    def mock_config(self, monkeypatch):
        """Create a mock config for testing."""
        # LLMModelRegistry accesses these config attributes during initialization
        # (see holmes/core/llm.py lines 490-497)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("MODEL", raising=False)
        config = MagicMock(spec=Config)
        config.should_try_robusta_ai = False
        config.model = None
        config.cluster_name = None
        config.api_base = None
        config.api_version = None
        config.api_key = None
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

    def _robusta_registry(self, mock_config, mock_dal, monkeypatch, gpt4o, boot_catalog):
        """A registry booted with `gpt4o` plus whatever `boot_catalog` carries,
        wired so refresh_robusta_models() (the ROB-707 resync path) is able to
        actually run a fetch."""
        mock_config.should_try_robusta_ai = True
        mock_config.cluster_name = "test-cluster"
        mock_dal.enabled = True
        mock_dal.account_id = "account-id"
        mock_dal.get_ai_credentials.return_value = ("account-id", "token")

        # ROBUSTA_AI=True bypasses _should_load_robusta_ai's "user already
        # provided a model list" skip, so `gpt4o` and the Robusta catalog can
        # coexist the way a real model_list.yaml + Robusta AI deployment does.
        monkeypatch.setattr(
            "holmes.core.llm.LLMModelRegistry._parse_models_file",
            lambda self, path: {"gpt4o": gpt4o},
        )
        with (
            patch("holmes.core.llm.ROBUSTA_AI", True),
            patch("holmes.core.llm.fetch_robusta_models", return_value=boot_catalog),
        ):
            return LLMModelRegistry(mock_config, mock_dal)

    def test_get_model_params_robusta_resync_behavior(
        self, mock_config, mock_dal, monkeypatch, gpt4o
    ):
        """get_model_params refreshes the Robusta catalog when the requested
        model isn't loaded, and serves it once the refresh finds it (ROB-707).
        """
        registry = self._robusta_registry(
            mock_config,
            mock_dal,
            monkeypatch,
            gpt4o,
            boot_catalog=RobustaModelsResponse(models={}),
        )

        with patch(
            "holmes.core.llm.fetch_robusta_models",
            return_value=RobustaModelsResponse(
                models={"Robusta/test": RobustaModel(model="sonnet-4")}
            ),
        ):
            model_params = registry.get_model_params("Robusta/test")

        assert model_params.model == "sonnet-4"
        assert model_params.name == "Robusta/test"

    def test_get_model_params_robusta_resync_still_not_found(
        self, mock_config, mock_dal, caplog, monkeypatch, gpt4o
    ):
        """get_model_params falls back to a loaded model when a refresh still
        can't find the requested one (ROB-707)."""
        boot_catalog = RobustaModelsResponse(
            models={"Robusta/opus-4-6": RobustaModel(model="bedrock/opus")}
        )
        registry = self._robusta_registry(
            mock_config, mock_dal, monkeypatch, gpt4o, boot_catalog
        )

        with caplog.at_level(logging.WARNING):
            with patch(
                "holmes.core.llm.fetch_robusta_models", return_value=boot_catalog
            ):
                model_params = registry.get_model_params("Robusta/non-existent")

        assert "Model Robusta/non-existent is not loaded; refreshing." in caplog.text
        error_msg = "Couldn't find model: Robusta/non-existent in model list"
        assert error_msg in caplog.text

        assert model_params.model == "gpt-4o"
        assert model_params.name == "gpt4o"

    def test_get_model_params_with_no_models_raises_helpful_error(
        self, mock_config, mock_dal, monkeypatch
    ):
        monkeypatch.setattr(
            "holmes.core.llm.LLMModelRegistry._parse_models_file",
            lambda self, path: {},
        )
        registry = LLMModelRegistry(mock_config, mock_dal)

        with pytest.raises(Exception) as exc:
            registry.get_model_params()

        error = str(exc.value)
        assert "No LLM models were loaded" in error
        assert "--model '<provider/model>'" in error
        assert "export MODEL='<provider/model>'" in error
        assert "MODEL_LIST_FILE_LOCATION/config model list" in error
        assert "is not enough without a model" in error

    def test_model_env_matching_model_list_keeps_full_model_entry(
        self, mock_config, mock_dal, monkeypatch
    ):
        model_key = "gemini-alias"
        model_entry = ModelEntry(
            model="gemini/gemini-2.0-flash",
            name=model_key,
            api_key=SecretStr("gemini-key"),
            api_base="https://generativelanguage.googleapis.com",
            custom_args={"temperature": 0.2},
        )
        monkeypatch.setattr(
            "holmes.core.llm.LLMModelRegistry._parse_models_file",
            lambda self, path: {model_key: model_entry},
        )
        monkeypatch.setenv("MODEL", model_key)
        mock_config.model = model_key

        registry = LLMModelRegistry(mock_config, mock_dal)

        loaded_entry = registry.models[model_key]
        assert loaded_entry.model == "gemini/gemini-2.0-flash"
        assert loaded_entry.name == model_key
        assert loaded_entry.api_key is not None
        assert loaded_entry.api_key.get_secret_value() == "gemini-key"
        assert loaded_entry.api_base == "https://generativelanguage.googleapis.com"
        assert loaded_entry.custom_args == {"temperature": 0.2}
