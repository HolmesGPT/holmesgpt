"""Signing key of the OAuth DB token store, read from Robusta's global_config."""

from unittest.mock import patch

from holmes.plugins.toolsets.mcp.oauth_token_store import DalTokenStore

_CONFIG_VALUE = "holmes.config.Config.get_robusta_global_config_value"


def test_plain_signing_key_is_used_as_is():
    with patch(_CONFIG_VALUE, return_value="plain-key"):
        assert DalTokenStore._get_signing_key() == "plain-key"


def test_env_template_signing_key_is_resolved(monkeypatch):
    monkeypatch.setenv("SIGNING_KEY", "real-key")
    with patch(_CONFIG_VALUE, return_value="{{ env.SIGNING_KEY }}"):
        assert DalTokenStore._get_signing_key() == "real-key"


def test_env_template_with_missing_env_var_disables_storage(monkeypatch):
    monkeypatch.delenv("SIGNING_KEY", raising=False)
    with patch(_CONFIG_VALUE, return_value="{{ env.SIGNING_KEY }}"):
        assert DalTokenStore._get_signing_key() is None


def test_no_signing_key():
    with patch(_CONFIG_VALUE, return_value=None):
        assert DalTokenStore._get_signing_key() is None
