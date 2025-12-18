from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
import yaml

from holmes.common.env_vars import ROBUSTA_API_ENDPOINT
from holmes.config import Config

# Global model configs for reuse across tests
SONNET_MODEL_CONFIG = {
    "aws_access_key_id": "test-access-key",
    "aws_region_name": "us-east-1",
    "aws_secret_access_key": "test-secret-key",
    "model": "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "temperature": 1,
    "thinking": {
        "budget_tokens": 10000,
        "type": "enabled",
    },
}

AZURE_MODEL_CONFIG = {
    "api_base": "https://test.openai.azure.com",
    "api_key": "test-azure-key",
    "api_version": "2025-01-01-preview",
    "model": "azure/gpt-5",
    "temperature": 0,
}


def _get_model_list_data():
    """Shared model list data with sonnet (Bedrock) and azure-5 (Azure) models."""
    return {
        "sonnet": SONNET_MODEL_CONFIG,
        "azure-5": AZURE_MODEL_CONFIG,
    }


@contextmanager
def _setup_model_list_file(monkeypatch, tmp_path, data=None):
    """Context manager to create and configure model list file, ensuring cleanup."""
    if data is None:
        data = _get_model_list_data()
    temp_config_file = tmp_path / "model_list.yaml"
    temp_config_file.write_text(yaml.dump(data))
    monkeypatch.setattr(
        "holmes.core.llm.MODEL_LIST_FILE_LOCATION", str(temp_config_file)
    )
    try:
        yield temp_config_file
    finally:
        if temp_config_file.exists():
            temp_config_file.unlink()


def _get_mock_llm():
    """Helper to create a mock LLM instance."""
    mock_llm_instance = MagicMock()
    mock_llm_instance.get_context_window_size.return_value = 128000
    mock_llm_instance.get_maximum_output_token.return_value = 4096
    return mock_llm_instance


def _assert_model_config_matches_call_kwargs(call_kwargs, model_config, model_name):
    """Assert that call_kwargs correctly uses all values from model_config."""
    assert call_kwargs["name"] == model_name

    for key, value in model_config.items():
        if key in ["model", "api_base", "api_version", "api_key"]:
            assert call_kwargs[key] == value
        else:
            assert call_kwargs["args"][key] == value


def test_load_custom_toolsets_config_valid(monkeypatch, tmp_path):
    temp_config_file = tmp_path / "custom_toolset.yaml"
    data = {
        "model1": {"model": "abc", "api-key": "asd"},
        "azure": {"model": "{{ env.TEST }}", "api-key": "ddd"},
        "bedrock": {"model": "bbbb", "api-key": "asfffd"},
    }
    temp_config_file.write_text(yaml.dump(data))
    monkeypatch.setattr(
        "holmes.core.llm.MODEL_LIST_FILE_LOCATION", str(temp_config_file)
    )
    monkeypatch.setenv("TEST", "test-value")

    config = Config()
    assert isinstance(config.llm_model_registry.models, dict)
    assert len(list(config.llm_model_registry.models.keys())) == 3
    az = config.llm_model_registry.models.get("azure")
    assert az.model == "test-value"


def test_config_load_model_list_valid_with_robusta_ai(server_config: Config):
    assert isinstance(server_config.llm_model_registry.models, dict)
    assert len(list(server_config.llm_model_registry.models.keys())) == 5
    assert (
        server_config.llm_model_registry.models["our_local_model"].model
        == "bedrock/custom_ai_model"
    )

    sonnet_model = server_config.llm_model_registry.models["Robusta/sonnet-4 preview"]

    assert (
        sonnet_model.base_url == f"{ROBUSTA_API_ENDPOINT}/llm/Robusta/sonnet-4 preview"
    )
    assert sonnet_model.name == "Robusta/sonnet-4 preview"
    assert sonnet_model.model == "claude-sonnet-4-20250514"
    assert sonnet_model.is_robusta_model

    assert (
        server_config.llm_model_registry.default_robusta_model
        == "Robusta/gpt-5-mini preview (minimal reasoning)"
    )


def test_model_list_file_location_env_var(monkeypatch, tmp_path):
    """Test that MODEL_LIST_FILE_LOCATION environment variable is used to load models."""
    with _setup_model_list_file(monkeypatch, tmp_path) as temp_config_file:
        monkeypatch.setenv("MODEL_LIST_FILE_LOCATION", str(temp_config_file))

        config = Config()

        assert isinstance(config.llm_model_registry.models, dict)
        assert len(config.llm_model_registry.models) == 2
        assert "sonnet" in config.llm_model_registry.models
        assert "azure-5" in config.llm_model_registry.models


@pytest.mark.parametrize(
    "model_name,model_config",
    [
        ("sonnet", SONNET_MODEL_CONFIG),
        ("azure-5", AZURE_MODEL_CONFIG),
    ],
)
def test_model_selection_uses_correct_params(
    monkeypatch, tmp_path, model_name, model_config
):
    """Test that selecting a model from the list uses the correct params for that model."""
    with _setup_model_list_file(monkeypatch, tmp_path):
        config = Config()

        with patch("holmes.config.DefaultLLM") as mock_default_llm:
            mock_default_llm.return_value = _get_mock_llm()
            config._get_llm(model_name)

            call_kwargs = mock_default_llm.call_args[1]
            _assert_model_config_matches_call_kwargs(
                call_kwargs, model_config, model_name
            )


@pytest.mark.parametrize(
    "model_name,model_config",
    [
        ("sonnet", SONNET_MODEL_CONFIG),
        ("azure-5", AZURE_MODEL_CONFIG),
    ],
)
def test_create_console_toolcalling_llm_with_model_from_list(
    monkeypatch, tmp_path, model_name, model_config
):
    """Test that create_console_toolcalling_llm correctly uses model from model list."""
    with _setup_model_list_file(monkeypatch, tmp_path):
        config = Config()

        with patch("holmes.config.DefaultLLM") as mock_default_llm, patch(
            "holmes.config.Config.create_console_tool_executor"
        ) as mock_tool_executor:
            mock_default_llm.return_value = _get_mock_llm()
            mock_tool_executor.return_value = MagicMock()

            config.create_console_toolcalling_llm(model_name=model_name)

            call_kwargs = mock_default_llm.call_args[1]
            _assert_model_config_matches_call_kwargs(
                call_kwargs, model_config, model_name
            )
