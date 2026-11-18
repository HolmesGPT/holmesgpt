"""An expired OAuth token in the token store must not be served again.

Covers IdPs that issue no refresh token (OpenShift OAuth): once the access token
is expired, the only recovery is a new sign-in.
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.oauth_config import MCPOAuthConfig
from holmes.plugins.toolsets.mcp.oauth_token_manager import OAuthTokenManager
from holmes.plugins.toolsets.mcp.oauth_token_store import DalTokenStore, OAuthTokenCache

USER_ID = "user-1"
AUTH_URL = "https://oauth-openshift.apps.example.com/oauth/authorize"
TOKEN_URL = "https://oauth-openshift.apps.example.com/oauth/token"
CONTEXT = {"user_id": USER_ID}


@pytest.fixture(autouse=True)
def _signing_key():
    with patch(
        "holmes.config.Config.get_robusta_global_config_value",
        return_value="expiry-test-signing-key",
    ):
        yield


@pytest.fixture
def oauth() -> MCPOAuthConfig:
    return MCPOAuthConfig(
        enabled=True,
        authorization_url=AUTH_URL,
        token_url=TOKEN_URL,
        client_id="mcp-openshift",
        scopes=["user:full"],
    )


def _fake_dal():
    """In-memory stand-in for the Supabase OAuth tokens table."""
    rows: dict = {}
    dal = MagicMock()

    def upsert(provider_name, encrypted_token, signing_key_hash, token_expiry, user_id):
        rows[(provider_name, user_id)] = {
            "provider_name": provider_name,
            "user_id": user_id,
            "encrypted_token": encrypted_token,
            "token_expiry": token_expiry,
        }

    dal.upsert_oauth_token.side_effect = upsert
    dal.get_oauth_token.side_effect = (
        lambda provider_name, user_id, signing_key_hash: rows.get((provider_name, user_id))
    )
    dal.get_all_oauth_tokens_for_cluster.side_effect = lambda signing_key_hash: list(rows.values())
    dal.delete_oauth_token.side_effect = (
        lambda provider_name, user_id, signing_key_hash: rows.pop((provider_name, user_id), None)
    )
    return dal, rows


def _manager_on(dal) -> OAuthTokenManager:
    """A manager with an empty cache over the given DB, i.e. a freshly started pod."""
    manager = OAuthTokenManager()
    manager._shutdown_event.set()
    manager._store = DalTokenStore(dal=dal)
    return manager


def _expire_stored_row(rows: dict) -> None:
    rows[(AUTH_URL, USER_ID)]["token_expiry"] = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat()


def _refresh_response(access_token: str) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"access_token": access_token, "expires_in": 3600}
    return response


class TestExpiredStoredToken:
    def test_expired_token_without_refresh_token_is_not_served(self, oauth):
        dal, rows = _fake_dal()
        manager = _manager_on(dal)
        manager.store_token(oauth, {"access_token": "sha256~old", "expires_in": 86400}, CONTEXT)

        _expire_stored_row(rows)
        cache_key = manager.get_cache_key(oauth, CONTEXT)
        manager.cache._cache[cache_key].expires_at = time.monotonic() - 1

        assert manager.get_access_token(oauth, CONTEXT) is None

    def test_expired_token_is_not_served_after_pod_restart(self, oauth):
        dal, rows = _fake_dal()
        _manager_on(dal).store_token(oauth, {"access_token": "sha256~old", "expires_in": 86400}, CONTEXT)
        _expire_stored_row(rows)

        restarted = _manager_on(dal)
        restarted.preload_from_store()

        assert restarted.get_access_token(oauth, CONTEXT) is None

    def test_expired_token_with_refresh_token_is_refreshed_before_use(self, oauth):
        dal, rows = _fake_dal()
        _manager_on(dal).store_token(
            oauth,
            {"access_token": "old-access", "expires_in": 3600, "refresh_token": "refresh-1"},
            CONTEXT,
        )
        _expire_stored_row(rows)

        restarted = _manager_on(dal)
        with patch(
            "holmes.plugins.toolsets.mcp.oauth_token_manager.httpx.post",
            return_value=_refresh_response("new-access"),
        ) as post:
            token = restarted.get_access_token(oauth, CONTEXT)

        assert token == "new-access"
        assert post.call_args.kwargs["data"]["refresh_token"] == "refresh-1"

    def test_failed_refresh_is_not_repeated_in_the_same_lookup(self, oauth):
        dal, rows = _fake_dal()
        manager = _manager_on(dal)
        manager.store_token(
            oauth,
            {"access_token": "old-access", "expires_in": 3600, "refresh_token": "refresh-1"},
            CONTEXT,
        )
        _expire_stored_row(rows)
        cache_key = manager.get_cache_key(oauth, CONTEXT)
        manager.cache._cache[cache_key].expires_at = time.monotonic() - 1

        failed = MagicMock()
        failed.status_code = 500
        with patch(
            "holmes.plugins.toolsets.mcp.oauth_token_manager.httpx.post", return_value=failed
        ) as post:
            token = manager.get_access_token(oauth, CONTEXT)

        assert token is None
        assert post.call_count == 1

    def test_token_about_to_expire_is_not_cached_past_its_expiry(self):
        cache = OAuthTokenCache()
        now = time.monotonic()
        cache.set("key", "short-lived", expires_in=5)

        with patch(
            "holmes.plugins.toolsets.mcp.oauth_token_store.time.monotonic", return_value=now + 6
        ):
            assert cache.get_valid_access_token("key") is None

    def test_unexpired_token_is_still_served_after_pod_restart(self, oauth):
        dal, _rows = _fake_dal()
        _manager_on(dal).store_token(oauth, {"access_token": "sha256~live", "expires_in": 86400}, CONTEXT)

        restarted = _manager_on(dal)
        restarted.preload_from_store()

        assert restarted.get_access_token(oauth, CONTEXT) == "sha256~live"
