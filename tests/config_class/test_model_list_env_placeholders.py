"""Model list entries whose {{ env.X }} placeholders are unset load independently."""

import logging

import pytest
import yaml

from holmes.config import Config

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
    for name in ALL_VARS + ["MODEL", "ROBUSTA_AI"]:
        monkeypatch.delenv(name, raising=False)

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
