from unittest.mock import MagicMock, patch

import pytest

from holmes.core import azure_token
from holmes.core.azure_token import get_azure_ad_token


@pytest.fixture(autouse=True)
def reset_token_cache():
    """Reset the module-level token cache between tests."""
    azure_token._cached_token = None
    azure_token._token_timestamp = 0.0
    yield
    azure_token._cached_token = None
    azure_token._token_timestamp = 0.0


class TestGetAzureAdToken:
    def test_returns_pre_acquired_token_from_env(self, monkeypatch):
        monkeypatch.setenv("AZURE_AD_TOKEN", "my-pre-acquired-token")
        assert get_azure_ad_token() == "my-pre-acquired-token"

    @patch("holmes.core.azure_token.get_bearer_token_provider")
    @patch("holmes.core.azure_token.DefaultAzureCredential")
    def test_falls_back_to_default_credential_when_env_not_set(
        self, mock_cred_cls, mock_provider_fn, monkeypatch
    ):
        monkeypatch.delenv("AZURE_AD_TOKEN", raising=False)
        mock_provider_fn.return_value = lambda: "credential-token"

        token = get_azure_ad_token()

        assert token == "credential-token"
        mock_cred_cls.assert_called_once()
        mock_provider_fn.assert_called_once()

    def test_pre_acquired_token_skips_default_credential(self, monkeypatch):
        monkeypatch.setenv("AZURE_AD_TOKEN", "injected-token")
        with patch("holmes.core.azure_token.DefaultAzureCredential") as mock_cred:
            token = get_azure_ad_token()
            assert token == "injected-token"
            mock_cred.assert_not_called()

    def test_empty_env_var_falls_back_to_default_credential(self, monkeypatch):
        monkeypatch.setenv("AZURE_AD_TOKEN", "")
        with patch("holmes.core.azure_token.DefaultAzureCredential"):
            with patch("holmes.core.azure_token.get_bearer_token_provider") as mock_provider:
                mock_provider.return_value = lambda: "fallback-token"
                token = get_azure_ad_token()
                assert token == "fallback-token"
