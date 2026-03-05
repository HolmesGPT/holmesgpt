import os
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from holmes.utils.github_app_token_manager import (
    GitHubAppTokenManager,
    ensure_github_app_token_env,
)


@pytest.fixture
def rsa_private_key():
    """Generate a real RSA private key for JWT signing."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def rsa_public_key(rsa_private_key):
    """Derive the public key for JWT verification."""
    key = load_pem_private_key(rsa_private_key.encode(), password=None)
    return key.public_key()


@pytest.fixture
def token_manager(rsa_private_key):
    return GitHubAppTokenManager(
        app_id="12345",
        installation_id="67890",
        private_key=rsa_private_key,
    )


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the singleton between tests."""
    GitHubAppTokenManager._instance = None
    yield
    GitHubAppTokenManager._instance = None


@pytest.fixture
def env_without_token():
    """Provide an environment with AUTO_GENERATED_GITHUB_TOKEN removed but GitHub App env vars present."""
    env = {k: v for k, v in os.environ.items() if k != "AUTO_GENERATED_GITHUB_TOKEN"}
    env.update({
        "GITHUB_APP_ID": "12345",
        "GITHUB_APP_INSTALLATION_ID": "67890",
        "GITHUB_APP_PRIVATE_KEY": "dummy-key",
    })
    with patch.dict(os.environ, env, clear=True):
        yield


class TestGitHubAppTokenManager:
    def test_generate_jwt(self, token_manager, rsa_public_key):
        encoded = token_manager._generate_jwt()
        decoded = jwt.decode(encoded, rsa_public_key, algorithms=["RS256"])

        assert decoded["iss"] == "12345"
        now = int(time.time())
        assert decoded["iat"] <= now
        assert decoded["exp"] > now

    @patch("holmes.utils.github_app_token_manager.requests.post")
    def test_refresh_token(self, mock_post, token_manager):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"token": "ghs_testtoken123", "expires_at": "2099-01-01T00:00:00Z"},
        )

        token = token_manager.refresh_token()
        assert token == "ghs_testtoken123"
        mock_post.assert_called_once()

    @patch("holmes.utils.github_app_token_manager.requests.post")
    def test_refresh_token_always_fetches(self, mock_post, token_manager):
        """Each call to refresh_token should make a new API call."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"token": "ghs_fresh", "expires_at": "2099-01-01T00:00:00Z"},
        )

        token1 = token_manager.refresh_token()
        token2 = token_manager.refresh_token()

        assert token1 == token2 == "ghs_fresh"
        assert mock_post.call_count == 2

    def test_from_env_returns_none_when_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            manager = GitHubAppTokenManager.from_env()
            assert manager is None

    def test_from_env_returns_manager_when_configured(self, rsa_private_key):
        env = {
            "GITHUB_APP_ID": "111",
            "GITHUB_APP_INSTALLATION_ID": "222",
            "GITHUB_APP_PRIVATE_KEY": rsa_private_key,
        }
        with patch.dict(os.environ, env, clear=False):
            manager = GitHubAppTokenManager.from_env()
            assert manager is not None
            assert manager._app_id == "111"
            assert manager._installation_id == "222"

    def test_from_env_returns_none_with_partial_config(self):
        env = {"GITHUB_APP_ID": "111"}
        with patch.dict(os.environ, env, clear=True):
            manager = GitHubAppTokenManager.from_env()
            assert manager is None


class TestEnsureGitHubAppTokenEnv:
    def test_does_not_override_existing_token(self):
        with patch.dict(
            os.environ, {"AUTO_GENERATED_GITHUB_TOKEN": "existing_token"}, clear=False
        ):
            ensure_github_app_token_env()
            assert os.environ["AUTO_GENERATED_GITHUB_TOKEN"] == "existing_token"

    @patch("holmes.utils.github_app_token_manager.GitHubAppTokenManager.get_instance")
    def test_sets_token_from_github_app(self, mock_get_instance, env_without_token):
        mock_manager = MagicMock()
        mock_manager.refresh_token.return_value = "ghs_generated"
        mock_get_instance.return_value = mock_manager

        ensure_github_app_token_env()
        assert os.environ["AUTO_GENERATED_GITHUB_TOKEN"] == "ghs_generated"

    @patch("holmes.utils.github_app_token_manager.GitHubAppTokenManager.get_instance")
    def test_handles_failure_gracefully(self, mock_get_instance, env_without_token):
        mock_manager = MagicMock()
        mock_manager.refresh_token.side_effect = Exception("API error")
        mock_get_instance.return_value = mock_manager

        ensure_github_app_token_env()
        assert "AUTO_GENERATED_GITHUB_TOKEN" not in os.environ

    @patch("holmes.utils.github_app_token_manager.GitHubAppTokenManager.get_instance")
    def test_noop_when_no_github_app_configured(self, mock_get_instance, env_without_token):
        mock_get_instance.return_value = None

        ensure_github_app_token_env()
        assert "AUTO_GENERATED_GITHUB_TOKEN" not in os.environ


class TestBackgroundRefresh:
    @patch("holmes.utils.github_app_token_manager.GitHubAppTokenManager.get_instance")
    def test_ensure_starts_background_thread(self, mock_get_instance, env_without_token):
        """ensure_github_app_token_env should start the background refresh thread."""
        mock_manager = MagicMock()
        mock_manager.refresh_token.return_value = "ghs_generated"
        mock_get_instance.return_value = mock_manager

        ensure_github_app_token_env()
        mock_manager.start_background_refresh.assert_called_once()
