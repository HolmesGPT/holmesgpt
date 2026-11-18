"""An OAuth token the provider no longer accepts must lead back to the login prompt.

Covers IdPs that issue no refresh token (OpenShift OAuth): once the access token
is expired or revoked, the only recovery is a new sign-in.
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import CallToolResult, TextContent

from holmes.core.oauth_config import MCPOAuthConfig
from holmes.core.oauth_utils import _get_token_manager
from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.mcp.oauth_token_manager import OAuthTokenManager
from holmes.plugins.toolsets.mcp.oauth_token_store import DalTokenStore, OAuthTokenCache
from holmes.plugins.toolsets.mcp.toolset_mcp import (
    MCPConfig,
    MCPMode,
    RemoteMCPTool,
    RemoteMCPToolset,
)

USER_ID = "user-1"
AUTH_URL = "https://oauth-openshift.apps.example.com/oauth/authorize"
TOKEN_URL = "https://oauth-openshift.apps.example.com/oauth/token"
CONTEXT = {"user_id": USER_ID}
K8S_401 = "failed to list namespaces: the server has asked for the client to provide credentials"


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


class TestTokenRejectedDuringToolCall:
    """Passthrough MCP servers report the API server's 401 as ordinary tool output."""

    @pytest.fixture
    def rows(self):
        dal, rows = _fake_dal()
        manager = _get_token_manager()
        saved_store = manager._store
        manager._store = DalTokenStore(dal=dal)
        yield rows
        manager._store = saved_store
        manager.cache.evict(manager._build_cache_key(USER_ID, AUTH_URL))

    @pytest.fixture
    def tool(self, oauth, rows) -> RemoteMCPTool:
        toolset = RemoteMCPToolset(name="kubernetes", enabled=True)
        toolset._mcp_config = MCPConfig(
            url="http://kubernetes-mcp:8080/mcp", mode=MCPMode.STREAMABLE_HTTP, oauth=oauth
        )
        _get_token_manager().store_token(
            oauth, {"access_token": "sha256~live", "expires_in": 86400}, CONTEXT
        )
        return RemoteMCPTool(
            name="namespaces_list", description="list namespaces", parameters={}, toolset=toolset
        )

    @staticmethod
    def _context(tool_call_id: str = "tc-1"):
        ctx = MagicMock()
        ctx.user_approved = False
        ctx.session_approved_prefixes = None
        ctx.tool_call_id = tool_call_id
        ctx.request_context = dict(CONTEXT)
        return ctx

    @staticmethod
    def _invoke(tool: RemoteMCPTool, text: str, is_error: bool, during_call=None):
        """Run the tool against a fake MCP session; during_call runs while the call is in flight."""

        async def call_tool(*_args, **_kwargs):
            if during_call:
                during_call()
            return CallToolResult(content=[TextContent(type="text", text=text)], isError=is_error)

        session = AsyncMock()
        session.call_tool = call_tool
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        with patch(
            "holmes.plugins.toolsets.mcp.toolset_mcp.get_initialized_mcp_session",
            return_value=session,
        ):
            return tool._invoke({}, TestTokenRejectedDuringToolCall._context())

    @pytest.mark.parametrize(
        "error_text",
        [
            K8S_401,
            "failed to list pods in namespace argo: Unauthorized",
            "request failed: HTTP 401 Unauthorized",
        ],
    )
    def test_rejected_token_brings_back_the_login_prompt(self, tool, rows, error_text):
        assert tool.requires_approval({}, self._context()) is None

        result = self._invoke(tool, error_text, is_error=True)

        assert result.status == StructuredToolResultStatus.ERROR
        assert error_text in result.data
        assert (AUTH_URL, USER_ID) not in rows
        approval = tool.requires_approval({}, self._context("tc-2"))
        assert approval is not None and approval.needs_approval

    def test_rejection_of_an_older_token_keeps_the_newer_one(self, tool, rows, oauth):
        """The user signed in again while a call with the old token was still in flight."""

        def sign_in_again():
            _get_token_manager().store_token(
                oauth, {"access_token": "sha256~newer", "expires_in": 86400}, CONTEXT
            )

        self._invoke(tool, K8S_401, is_error=True, during_call=sign_in_again)

        assert _get_token_manager().get_access_token(oauth, CONTEXT) == "sha256~newer"
        assert (AUTH_URL, USER_ID) in rows

    def test_no_removal_is_reported_when_no_token_was_sent(self, tool, oauth):
        _get_token_manager().invalidate_token(oauth, CONTEXT, "sha256~live")

        result = self._invoke(tool, K8S_401, is_error=True)

        assert result.data == K8S_401

    def test_no_removal_is_reported_when_the_stored_token_cannot_be_deleted(self, tool, rows):
        store = _get_token_manager()._store
        store._dal.delete_oauth_token.side_effect = RuntimeError("db unavailable")

        result = self._invoke(tool, K8S_401, is_error=True)

        assert (AUTH_URL, USER_ID) in rows
        assert result.data == K8S_401

    @pytest.mark.parametrize(
        "error_text",
        [
            'pods is forbidden: User "jane" cannot list resource "pods" in namespace "argo"',
            'failed to get pod: pods "api-401" not found',
            'failed to get pod: pods "unauthorized-proxy" not found',
        ],
    )
    def test_other_errors_keep_the_token(self, tool, rows, error_text):
        self._invoke(tool, error_text, is_error=True)

        assert (AUTH_URL, USER_ID) in rows
        assert tool.requires_approval({}, self._context("tc-2")) is None

    def test_successful_output_mentioning_unauthorized_keeps_the_token(self, tool, rows):
        self._invoke(tool, "Warning  Failed  kubelet  pull image: 401 Unauthorized", is_error=False)

        assert (AUTH_URL, USER_ID) in rows
        assert tool.requires_approval({}, self._context("tc-2")) is None
