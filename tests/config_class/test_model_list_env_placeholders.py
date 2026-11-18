"""Model list entries whose {{ env.X }} placeholders are unset load independently."""

import logging

import pytest
import yaml

from holmes.config import Config
from holmes.core.llm import ModelEntry

OPENROUTER_MODEL = {
    "model": "openrouter/moonshotai/kimi-k3",
    "api_key": "{{ env.TEST_OPENROUTER_KEY }}",
}
BEDROCK_MODEL = {
    "model": "bedrock/us.anthropic.claude-opus-5-5",
    "aws_access_key_id": "{{ env.TEST_AWS_KEY_ID }}",
    "aws_secret_access_key": "{{ env.TEST_AWS_SECRET }}",
    "aws_region_name": "us-east-1",
}
AZURE_MODEL = {
    "model": "azure/responses/gpt-6.1-sol",
    "api_key": "{{ env.TEST_AZURE_KEY }}",
    "api_base": "{{ env.TEST_AZURE_BASE }}",
    "api_version": "2025-04-01-preview",
}
ALL_VARS = [
    "TEST_OPENROUTER_KEY",
    "TEST_AWS_KEY_ID",
    "TEST_AWS_SECRET",
    "TEST_AZURE_KEY",
    "TEST_AZURE_BASE",
]


@pytest.fixture
def model_list(monkeypatch, tmp_path):
    for name in [*ALL_VARS, "MODEL", "OPENAI_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    # Read at import, so clearing the env var alone doesn't reset it.
    monkeypatch.setattr("holmes.core.llm.ROBUSTA_AI", None)

    def write(data):
        path = tmp_path / "model_list.yaml"
        path.write_text(yaml.dump(data))
        monkeypatch.setattr("holmes.core.llm.MODEL_LIST_FILE_LOCATION", str(path))
        return path

    return write


def test_all_variables_set_loads_every_model(model_list, monkeypatch):
    model_list({"kimi": OPENROUTER_MODEL, "opus": BEDROCK_MODEL, "sol": AZURE_MODEL})
    for name in ALL_VARS:
        monkeypatch.setenv(name, f"value-{name}")

    registry = Config().llm_model_registry

    assert list(registry.models) == ["kimi", "opus", "sol"]
    assert registry.unavailable_models == {}
    sol = registry.get_model_params("sol")
    assert sol.api_key.get_secret_value() == "value-TEST_AZURE_KEY"
    assert sol.api_base == "value-TEST_AZURE_BASE"


def test_missing_variable_skips_only_its_model(model_list, monkeypatch, caplog):
    path = model_list(
        {"kimi": OPENROUTER_MODEL, "opus": BEDROCK_MODEL, "sol": AZURE_MODEL}
    )
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "or-key")
    monkeypatch.setenv("TEST_AWS_KEY_ID", "id")

    with caplog.at_level(logging.WARNING):
        registry = Config().llm_model_registry
        Config().llm_model_registry

    assert list(registry.models) == ["kimi"]
    assert registry.get_model_params("kimi").api_key.get_secret_value() == "or-key"
    assert registry.unavailable_models == {
        "opus": ["TEST_AWS_SECRET"],
        "sol": ["TEST_AZURE_BASE", "TEST_AZURE_KEY"],
    }
    assert caplog.text.count(f"Model 'opus' from {path} is not loaded") == 1
    assert "TEST_AWS_SECRET not set" in caplog.text


def test_requesting_unavailable_model_names_missing_variables(model_list, monkeypatch):
    path = model_list({"kimi": OPENROUTER_MODEL, "opus": BEDROCK_MODEL})
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "or-key")
    config = Config()

    with pytest.raises(ValueError) as exc:
        config._get_llm(model_key="opus")

    assert str(exc.value) == (
        f"Model 'opus' is defined in {path} but was not loaded: "
        "environment variable(s) TEST_AWS_KEY_ID, TEST_AWS_SECRET not set"
    )


def test_empty_variable_counts_as_unset(model_list, monkeypatch):
    # GitHub Actions passes an unset secret as an empty string.
    model_list({"kimi": OPENROUTER_MODEL, "sol": AZURE_MODEL})
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "")
    monkeypatch.setenv("TEST_AZURE_KEY", "az-key")
    monkeypatch.setenv("TEST_AZURE_BASE", "https://example.invalid")

    registry = Config().llm_model_registry

    assert list(registry.models) == ["sol"]
    assert registry.unavailable_models == {"kimi": ["TEST_OPENROUTER_KEY"]}


def test_unavailable_model_is_not_loaded_as_raw_config_model(model_list, monkeypatch):
    model_list({"kimi": OPENROUTER_MODEL, "opus": BEDROCK_MODEL})
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "or-key")

    config = Config(model="opus")

    assert "opus" not in config.llm_model_registry.models
    with pytest.raises(ValueError, match="TEST_AWS_KEY_ID, TEST_AWS_SECRET not set"):
        config._get_llm(model_key="opus")


def test_every_model_unavailable_does_not_fall_back_to_robusta_ai(
    model_list, monkeypatch
):
    model_list({"opus": BEDROCK_MODEL, "sol": AZURE_MODEL})
    config = Config()
    config.should_try_robusta_ai = True

    registry = config.llm_model_registry

    assert registry.models == {}
    assert not registry._should_load_robusta_ai()
    with pytest.raises(Exception) as exc:
        registry.get_model_params()
    assert "needs an environment variable that is not set" in str(exc.value)
    assert "opus: TEST_AWS_KEY_ID, TEST_AWS_SECRET" in str(exc.value)
    assert "sol: TEST_AZURE_BASE, TEST_AZURE_KEY" in str(exc.value)


def test_reload_picks_up_newly_set_variable(model_list, monkeypatch):
    model_list({"kimi": OPENROUTER_MODEL, "opus": BEDROCK_MODEL})
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "or-key")
    config = Config()
    assert "opus" in config.llm_model_registry.unavailable_models

    monkeypatch.setenv("TEST_AWS_KEY_ID", "id")
    monkeypatch.setenv("TEST_AWS_SECRET", "secret")
    config.reload_models()

    assert list(config.llm_model_registry.models) == ["kimi", "opus"]
    assert config.llm_model_registry.unavailable_models == {}


def test_placeholder_in_nested_value_is_checked(model_list, monkeypatch):
    nested = {
        "model": "openrouter/z-ai/glm-5.3",
        "api_key": "literal",
        "extra_headers": {"X-Team": "{{ env.TEST_TEAM }}"},
        "fallbacks": ["{{ env.TEST_FALLBACK }}"],
    }
    model_list({"glm": nested})
    monkeypatch.delenv("TEST_TEAM", raising=False)
    monkeypatch.delenv("TEST_FALLBACK", raising=False)

    registry = Config().llm_model_registry

    assert registry.unavailable_models == {"glm": ["TEST_FALLBACK", "TEST_TEAM"]}


def test_configured_unavailable_model_is_not_replaced_by_another(
    model_list, monkeypatch
):
    model_list({"kimi": OPENROUTER_MODEL, "opus": BEDROCK_MODEL})
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "or-key")
    registry = Config(model="opus").llm_model_registry

    with pytest.raises(ValueError, match="Model 'opus' is defined in"):
        registry.get_model_params()
    assert registry.get_model_params("kimi").model == OPENROUTER_MODEL["model"]


def test_no_configured_model_falls_back_to_first_available(model_list, monkeypatch):
    model_list({"opus": BEDROCK_MODEL, "kimi": OPENROUTER_MODEL})
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "or-key")

    registry = Config().llm_model_registry

    assert registry.get_model_params().model == OPENROUTER_MODEL["model"]


def test_loaded_model_of_the_same_name_wins_over_a_skipped_entry(
    model_list, monkeypatch
):
    # e.g. a hosted Robusta model installed under a name the file also uses
    model_list({"shared-name": BEDROCK_MODEL})
    registry = Config().llm_model_registry
    registry._llms["shared-name"] = ModelEntry(model="openai/hosted")

    assert registry.get_model_params("shared-name").model == "openai/hosted"


def test_legacy_openai_default_does_not_replace_a_skipped_entry(
    model_list, monkeypatch
):
    model_list({"gpt-5.4": AZURE_MODEL})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    registry = Config().llm_model_registry

    assert "gpt-5.4" not in registry.models
    with pytest.raises(ValueError, match="TEST_AZURE_BASE, TEST_AZURE_KEY not set"):
        registry.get_model_params("gpt-5.4")


def test_placeholder_in_nested_list_is_substituted(model_list, monkeypatch):
    model_list(
        {
            "glm": {
                "model": "openrouter/z-ai/glm-5.3",
                "extra_body": {"matrix": [["{{ env.TEST_LABEL }}", "fixed"]]},
            }
        }
    )
    monkeypatch.setenv("TEST_LABEL", "team-a")

    entry = Config().llm_model_registry.get_model_params("glm")

    assert entry.model_extra["extra_body"] == {"matrix": [["team-a", "fixed"]]}
