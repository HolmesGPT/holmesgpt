"""Tests for operator configuration loading."""

import importlib

import pytest

from holmes_operator import config as config_module


@pytest.fixture
def reload_config():
    """Reload the config module so environment changes take effect."""

    def _reload():
        return importlib.reload(config_module)

    yield _reload

    importlib.reload(config_module)


def test_api_key_is_none_when_not_set(monkeypatch, reload_config):
    monkeypatch.delenv("HOLMES_API_KEY", raising=False)

    config = reload_config().OperatorConfig.load()

    assert config.holmes_api_key is None


def test_api_key_is_read_from_environment(monkeypatch, reload_config):
    monkeypatch.setenv("HOLMES_API_KEY", "secret-key")

    config = reload_config().OperatorConfig.load()

    assert config.holmes_api_key == "secret-key"
