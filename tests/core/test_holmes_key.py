import json
from unittest.mock import patch

import pytest

from holmes.core.holmes_key import get_public_key, get_signing_key, open_sealed

_CONFIG_VALUE = "holmes.config.Config.get_robusta_global_config_value"

# Made by the relay's seal_for_holmes for the signing key "test-signing-key".
PUBLIC_KEY = "lTdVLARiFKBK8ftYKeucstZtS5PJvBGXxPEt4rS0wR8="
SEALED = (
    "QmrKVqfSKLVnxJbNOIYgv1VDGmtoToeQYG7hxPVf+mqiC78DHdb1lvM/PnsS+tWddL7jIpIR0bU5"
    "GmMzgnbZl6QBdsBOIDPxjssrq0+yC0H0acieizjhisBh3PBecJrOywhFZpyG44+LSuhweF9Mjdzl"
    "uveSy50YIsPErCf+BkEJxT8m+4fJg93/Vi+AzFWdRU/c4BU1DoZ0KGWLZxn2keYKBNUVLn7MtiET"
    "nv2FLvGpUjoL"
)
SEALED_SECRETS = {
    "code": "auth-code-SECRET-1234",
    "code_verifier": "verifier-SECRET-5678",
    "client_secret": "client-secret-SECRET-9012",
}


def test_plain_signing_key_is_used_as_is():
    with patch(_CONFIG_VALUE, return_value="plain-key"):
        assert get_signing_key() == "plain-key"


def test_env_template_signing_key_is_resolved(monkeypatch):
    monkeypatch.setenv("SIGNING_KEY", "real-key")
    with patch(_CONFIG_VALUE, return_value="{{ env.SIGNING_KEY }}"):
        assert get_signing_key() == "real-key"


def test_env_template_with_missing_env_var_has_no_key(monkeypatch):
    monkeypatch.delenv("SIGNING_KEY", raising=False)
    with patch(_CONFIG_VALUE, return_value="{{ env.SIGNING_KEY }}"):
        assert get_signing_key() is None


def test_no_signing_key_has_no_public_key():
    with patch(_CONFIG_VALUE, return_value=None):
        assert get_signing_key() is None
        assert get_public_key() is None
        with pytest.raises(ValueError):
            open_sealed(SEALED)


def test_public_key_comes_from_the_signing_key():
    with patch(_CONFIG_VALUE, return_value="test-signing-key"):
        assert get_public_key() == PUBLIC_KEY
    with patch(_CONFIG_VALUE, return_value="other-key"):
        assert get_public_key() != PUBLIC_KEY


def test_opens_a_value_sealed_by_the_relay():
    with patch(_CONFIG_VALUE, return_value="test-signing-key"):
        assert json.loads(open_sealed(SEALED)) == SEALED_SECRETS


def test_another_signing_key_cannot_open_it():
    with patch(_CONFIG_VALUE, return_value="other-key"):
        with pytest.raises(Exception):
            open_sealed(SEALED)
