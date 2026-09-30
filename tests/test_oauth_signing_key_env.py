"""ROBUSTA_SIGNING_KEY env fallback for the OAuth DB token store.

A standalone Holmes install (no runner, no robusta config file) supplies the
signing key through the environment instead of global_config.signing_key.
"""

import hashlib
from unittest.mock import MagicMock, patch

from holmes.plugins.toolsets.mcp.oauth_token_store import DalTokenStore

_STORE_MODULE = "holmes.plugins.toolsets.mcp.oauth_token_store"
_CONFIG_LOOKUP = "holmes.config.Config.get_robusta_global_config_value"


def test_env_signing_key_takes_precedence_over_config_file():
    with patch(f"{_STORE_MODULE}.ROBUSTA_SIGNING_KEY", "env-key"), patch(
        _CONFIG_LOOKUP, return_value="file-key"
    ) as lookup:
        assert DalTokenStore._get_signing_key() == "env-key"
        lookup.assert_not_called()


def test_config_file_used_when_env_unset():
    with patch(f"{_STORE_MODULE}.ROBUSTA_SIGNING_KEY", ""), patch(
        _CONFIG_LOOKUP, return_value="file-key"
    ) as lookup:
        assert DalTokenStore._get_signing_key() == "file-key"
        lookup.assert_called_once_with("signing_key")


def test_no_key_anywhere_disables_db_store():
    with patch(f"{_STORE_MODULE}.ROBUSTA_SIGNING_KEY", ""), patch(
        _CONFIG_LOOKUP, return_value=None
    ):
        store = DalTokenStore(dal=MagicMock())
        assert store._get_signing_key_hash() is None
        assert store._encrypt_token({"access_token": "x"}) is None


def test_env_key_hash_and_encryption_roundtrip():
    with patch(f"{_STORE_MODULE}.ROBUSTA_SIGNING_KEY", "env-key"), patch(
        _CONFIG_LOOKUP, return_value=None
    ):
        store = DalTokenStore(dal=MagicMock())
        assert store._get_signing_key_hash() == hashlib.sha256(b"env-key").hexdigest()
        encrypted = store._encrypt_token({"access_token": "abc"})
        assert encrypted is not None
        assert store._decrypt_token(encrypted) == {"access_token": "abc"}


def test_env_var_is_read_and_stripped(monkeypatch):
    import importlib

    import holmes.common.env_vars as env_vars

    monkeypatch.setenv("ROBUSTA_SIGNING_KEY", "  padded-key \n")
    try:
        reloaded = importlib.reload(env_vars)
        assert reloaded.ROBUSTA_SIGNING_KEY == "padded-key"
    finally:
        monkeypatch.delenv("ROBUSTA_SIGNING_KEY", raising=False)
        importlib.reload(env_vars)
