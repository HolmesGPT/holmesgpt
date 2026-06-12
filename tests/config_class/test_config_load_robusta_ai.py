from unittest.mock import patch

from holmes.clients.robusta_client import RobustaModel, RobustaModelsResponse
from holmes.config import Config
from holmes.core.llm import ModelEntry


def _fake_existing_model_entry() -> ModelEntry:
    return ModelEntry(model="gpt-4o", base_url="http://foo")

ROBUSTA_TEST_MODELS = RobustaModelsResponse(
    models={
        "Robusta/test": RobustaModel(
            holmes_args={},
            model="azure/gpt-4o",
            is_default=False,
            metadata={"min_litellm_version": "1.78.0"},
        )
    }
)


@patch("holmes.core.llm.ROBUSTA_AI", True)
def test_cli_not_loading_robusta_ai(*, monkeypatch):
    config = Config.load_from_file(None)
    assert "Robusta" not in config.llm_model_registry.models


@patch("holmes.core.llm.ROBUSTA_AI", True)
@patch("holmes.core.llm.fetch_robusta_models", return_value=ROBUSTA_TEST_MODELS)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_server_loads_robusta_ai_when_true(mock_cluster, mock_fetch, *, monkeypatch):
    config = Config.load_from_env()
    assert "Robusta/test" in config.llm_model_registry.models


@patch("holmes.core.llm.ROBUSTA_AI", None)
@patch("holmes.core.llm.fetch_robusta_models", return_value=ROBUSTA_TEST_MODELS)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_server_loads_robusta_ai_when_not_exists_and_not_other_models(
    mock_cluster, mock_fetch, *, monkeypatch
):
    monkeypatch.setattr("holmes.core.llm.MODEL_LIST_FILE_LOCATION", "")
    config = Config.load_from_env()
    assert "Robusta/test" in config.llm_model_registry.models


@patch("holmes.core.llm.ROBUSTA_AI", False)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_server_not_loads_robusta_ai_when_false(mock_cluster, *, monkeypatch):
    config = Config.load_from_env()
    assert "Robusta" not in config.llm_model_registry.models


@patch("holmes.core.llm.ROBUSTA_AI", True)
@patch("holmes.core.llm.fetch_robusta_models", return_value=ROBUSTA_TEST_MODELS)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
@patch(
    "holmes.core.llm.LLMModelRegistry._parse_models_file",
    return_value={"existing_model": _fake_existing_model_entry()},
)
def test_server_loads_robusta_ai_when_true_and_model_list_exists(
    mock_parse, mock_cluster, mock_fetch, *, monkeypatch
):
    config = Config.load_from_env()
    assert "existing_model" in config.llm_model_registry.models
    assert "Robusta/test" in config.llm_model_registry.models


@patch("holmes.core.llm.ROBUSTA_AI", False)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
@patch(
    "holmes.core.llm.LLMModelRegistry._parse_models_file",
    return_value={"existing_model": _fake_existing_model_entry()},
)
def test_server_not_loads_robusta_ai_when_false_and_model_list_exists(
    mock_parse, mock_cluster, *, monkeypatch
):
    config = Config.load_from_env()
    assert "existing_model" in config.llm_model_registry.models
    assert "Robusta" not in config.llm_model_registry.models


@patch("holmes.core.llm.ROBUSTA_AI", None)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
@patch(
    "holmes.core.llm.LLMModelRegistry._parse_models_file",
    return_value={"existing_model": _fake_existing_model_entry()},
)
def test_server_not_loads_robusta_ai_when_no_env_var_and_model_list_exists(
    mock_parse, mock_cluster, *, monkeypatch
):
    config = Config.load_from_env()
    assert "existing_model" in config.llm_model_registry.models
    assert "Robusta" not in config.llm_model_registry.models


@patch("holmes.core.llm.ROBUSTA_AI", True)
@patch("holmes.core.llm.fetch_robusta_models", return_value=ROBUSTA_TEST_MODELS)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_server_loads_robusta_ai_when_model_var_exists(
    mock_cluster, mock_fetch, *, monkeypatch
):
    monkeypatch.setenv("MODEL", "some_model")

    config = Config.load_from_env()
    assert "Robusta/test" in config.llm_model_registry.models


@patch("holmes.core.llm.ROBUSTA_AI", None)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_server_not_loads_robusta_ai_when_model_var_exists_and_no_env_var(
    mock_cluster, *, monkeypatch
):
    monkeypatch.setenv("MODEL", "some_model")
    config = Config.load_from_env()
    assert "Robusta" not in config.llm_model_registry.models


@patch("holmes.core.llm.ROBUSTA_AI", False)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_server_not_loads_robusta_ai_when_model_var_exists_and_false_env_var(
    mock_cluster, *, monkeypatch
):
    monkeypatch.setenv("MODEL", "some_model")
    config = Config.load_from_env()
    assert "Robusta" not in config.llm_model_registry.models


ROBUSTA_HOLMES_ARGS_MODELS = RobustaModelsResponse(
    models={
        "Robusta/test": RobustaModel(
            holmes_args={},
            model="azure/gpt-4o",
            is_default=False,
            metadata={"min_litellm_version": "1.78.0"},
        ),
        "Robusta/sonnet-1m": RobustaModel(
            holmes_args={"max_context_size": 1000000},
            model="claude-sonnet-4-20250514",
            is_default=False,
            metadata={"min_litellm_version": "1.78.0"},
        ),
    }
)


@patch("holmes.core.llm.ROBUSTA_AI", True)
@patch("holmes.core.llm.fetch_robusta_models", return_value=ROBUSTA_HOLMES_ARGS_MODELS)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_robusta_ai_config_get_llm_context_override(
    mock_parse, mock_cluster, *, monkeypatch
):
    """Test that relay holmes_args fields are passed and used for max_context_size.
    Also makes sure the args are poped before getting to completion call llm"""
    config = Config.load_from_env()
    llm = config._get_llm("Robusta/sonnet-1m")
    assert llm.get_context_window_size() == 1000000
    assert llm.args.get("custom_args") is None


# --- LiteLLM compatibility self-gating ---------------------------------------

ROBUSTA_GATED_MODELS = RobustaModelsResponse(
    models={
        "Robusta/compatible": RobustaModel(
            holmes_args={},
            model="azure/gpt-4o",
            is_default=True,
            metadata={"min_litellm_version": "1.78.0"},
        ),
        "Robusta/too-new": RobustaModel(
            holmes_args={},
            model="anthropic/claude-opus-4-8",
            is_default=False,
            metadata={"min_litellm_version": "1.999.0"},
        ),
        "Robusta/undeclared": RobustaModel(
            holmes_args={},
            model="some/model",
            is_default=False,
        ),
    }
)


@patch("holmes.core.llm.ROBUSTA_AI", True)
@patch("holmes.core.llm.fetch_robusta_models", return_value=ROBUSTA_GATED_MODELS)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_robusta_ai_skips_litellm_incompatible_models(
    mock_cluster, mock_fetch, *, monkeypatch
):
    """Models requiring a newer litellm, or with no declared minimum, are hidden."""
    config = Config.load_from_env()
    models = config.llm_model_registry.models
    assert "Robusta/compatible" in models
    # Requires litellm 1.999.0 -> skipped on the installed (older) litellm.
    assert "Robusta/too-new" not in models
    # No declared min_litellm_version -> fail closed.
    assert "Robusta/undeclared" not in models


ROBUSTA_ALL_INCOMPATIBLE = RobustaModelsResponse(
    models={
        "Robusta/too-new-1": RobustaModel(
            holmes_args={}, model="m1", metadata={"min_litellm_version": "1.999.0"}
        ),
        "Robusta/too-new-2": RobustaModel(
            holmes_args={}, model="m2", metadata={"min_litellm_version": "2.0.0"}
        ),
    }
)


@patch("holmes.core.llm.ROBUSTA_AI", True)
@patch("holmes.core.llm.fetch_robusta_models", return_value=ROBUSTA_ALL_INCOMPATIBLE)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test")
def test_robusta_ai_falls_back_when_all_models_filtered(
    mock_cluster, mock_fetch, *, monkeypatch
):
    """Safety net: if gating hides everything, fall back to the default model."""
    from holmes.core.llm import ROBUSTA_AI_MODEL_NAME

    config = Config.load_from_env()
    models = config.llm_model_registry.models
    assert "Robusta/too-new-1" not in models
    assert "Robusta/too-new-2" not in models
    # Holmes isn't left empty — the default Robusta model is loaded instead.
    assert ROBUSTA_AI_MODEL_NAME in models
