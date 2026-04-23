"""Tests for MCP OAuth authorization_code support."""

import asyncio
import json
import secrets
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import ConfigDict

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    Toolset,
    ToolsetStatusEnum,
    ToolsetTag,
)
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.oauth_config import (
    MCPOAuthConfig,
    OAuthDecisionCode,
    OAuthEndpoints,
    OAuthExchangeManager,
    _get_exchange_manager,
    parse_oauth_decision,
)
from holmes.core.oauth_utils import (
    _get_token_manager,
    cli_oauth_flow,
    generate_pkce,
)
from holmes.plugins.toolsets.mcp.oauth_token_manager import OAuthTokenManager
from holmes.plugins.toolsets.mcp.oauth_token_manager import _get_user_id
from holmes.plugins.toolsets.mcp.oauth_token_store import _CachedToken
from holmes.plugins.toolsets.mcp.oauth_token_store import DiskTokenStore, OAuthTokenCache
from holmes.plugins.toolsets.mcp.toolset_mcp import (
    MCPConfig,
    MCPMode,
    RemoteMCPTool,
    RemoteMCPToolset,
)


@pytest.fixture(autouse=True)
def _reset_token_manager_store():
    """Prevent mock stores from leaking across tests via the singleton token manager."""
    mgr = _get_token_manager()
    saved = mgr._store
    yield
    mgr._store = saved


class TestMCPOAuthConfig:
    def test_oauth_config_parsing(self):
        config = MCPConfig(
            url="http://example.com:8000",
            mode=MCPMode.STREAMABLE_HTTP,
            oauth=MCPOAuthConfig(
                enabled=True,
                authorization_url="http://auth.example.com/authorize",
                token_url="http://auth.example.com/token",
                client_id="my-client",
                scopes=["mcp:tools", "read"],
            ),
        )
        assert config.oauth is not None
        assert config.oauth.authorization_url == "http://auth.example.com/authorize"
        assert config.oauth.token_url == "http://auth.example.com/token"
        assert config.oauth.client_id == "my-client"
        assert config.oauth.scopes == ["mcp:tools", "read"]

    def test_oauth_config_default_none(self):
        config = MCPConfig(url="http://example.com:8000")
        assert config.oauth is None

    def test_oauth_config_defaults(self):
        oauth = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://auth/authorize",
            token_url="http://auth/token",
            client_id="cid",
        )
        assert oauth.scopes is None


class TestPKCE:
    def test_generate_pkce(self):
        verifier, challenge = generate_pkce()
        assert len(verifier) <= 128
        assert len(verifier) >= 43
        assert len(challenge) > 0
        # Challenge should be base64url-encoded (no padding)
        assert "=" not in challenge
        assert "+" not in challenge
        assert "/" not in challenge

    def test_pkce_different_each_time(self):
        v1, c1 = generate_pkce()
        v2, c2 = generate_pkce()
        assert v1 != v2
        assert c1 != c2


class TestOAuthTokenCache:
    def test_set_and_get(self):
        cache = OAuthTokenCache()
        cache.set("conv-1", "token-abc", expires_in=60)
        assert cache.get_valid_access_token("conv-1") == "token-abc"

    def test_has(self):
        cache = OAuthTokenCache()
        assert not cache.has_token_or_refresh("conv-1")
        cache.set("conv-1", "token-abc", expires_in=60)
        assert cache.has_token_or_refresh("conv-1")

    def test_expired_entry(self):
        cache = OAuthTokenCache()
        # Set with 0 expires_in — the code does max(expires_in - 30, 10) so minimum is 10s
        # Instead, directly manipulate the cache entry to test expiry
        cache.set("conv-exp", "token-abc", expires_in=31)  # will be 1 second after buffer
        # Manually expire it
        cache._cache["conv-exp"].expires_at = time.monotonic() - 1
        cache._cache["conv-exp"].refresh_expires_at = time.monotonic() - 1
        assert cache.get_valid_access_token("conv-exp") is None
        assert not cache.has_token_or_refresh("conv-exp")

    def test_different_conversations(self):
        cache = OAuthTokenCache()
        cache.set("conv-1", "token-1", expires_in=60)
        cache.set("conv-2", "token-2", expires_in=60)
        assert cache.get_valid_access_token("conv-1") == "token-1"
        assert cache.get_valid_access_token("conv-2") == "token-2"



class TestRequiresApproval:
    def _make_tool(self, oauth_config=None):
        toolset = RemoteMCPToolset(name="test-oauth", enabled=True)
        toolset._mcp_config = MCPConfig(
            url="http://mcp-server:8000",
            mode=MCPMode.STREAMABLE_HTTP,
            oauth=oauth_config,
        )
        return RemoteMCPTool(
            name="test_tool",
            description="test",
            parameters={},
            toolset=toolset,
        )

    def _make_context(self, conv_id="test-conv", tool_call_id="tc-123"):
        ctx = MagicMock()
        ctx.user_approved = False
        ctx.tool_call_id = tool_call_id
        ctx.request_context = {"headers": {"X-Conversation-Id": conv_id}}
        return ctx

    def test_requires_approval_with_oauth_metadata(self):
        oauth = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://keycloak/authorize",
            token_url="http://keycloak/token",
            client_id="cid",
            scopes=["mcp:tools"],
        )
        tool = self._make_tool(oauth)
        context = self._make_context(conv_id="approval-test-1")
        # Clear any cached token and ensure store returns nothing
        cache_key = _get_token_manager().get_cache_key(oauth, context.request_context)
        _get_token_manager().cache.evict(cache_key)

        params = {"a": 1}
        _get_token_manager()._store = MagicMock()
        _get_token_manager()._store.get_token.return_value = None
        result = tool.requires_approval(params, context)

        assert result is not None
        assert result.needs_approval is True
        assert "OAuth authentication required" in result.reason

        meta = params["__oauth_metadata"]
        assert meta["authorization_url"] == "http://keycloak/authorize"
        assert meta["client_id"] == "cid"
        assert meta["scopes"] == ["mcp:tools"]
        assert meta["code_challenge_method"] == "S256"
        assert len(meta["code_challenge"]) > 0
        # encryption_public_key removed — frontend sends auth code as plaintext JSON
        assert "encryption_public_key" not in meta
        # token_url should NOT be sent to frontend
        assert "token_url" not in meta

    def test_no_approval_when_token_cached(self):
        oauth = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://keycloak/authorize",
            token_url="http://keycloak/token",
            client_id="cid",
        )
        tool = self._make_tool(oauth)
        context = self._make_context(conv_id="cached-conv-2")
        # Cache using the real cache key (conv + idp hash)
        cache_key = _get_token_manager().get_cache_key(oauth, context.request_context)
        _get_token_manager().cache.set(cache_key, "some-token")

        result = tool.requires_approval({}, context)
        assert result is None

    def test_no_approval_without_oauth(self):
        tool = self._make_tool(oauth_config=None)
        context = self._make_context()
        result = tool.requires_approval({}, context)
        assert result is None


class TestExchangeCodeForToken:
    def test_full_flow(self):
        """Simulate: Holmes generates PKCE → frontend sends auth code as JSON → Holmes exchanges for token."""
        code_verifier = "test-verifier-12345"
        tool_call_id = "tc-full-flow"
        conv_id = "conv-full-flow"

        oauth_config = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://keycloak/authorize",
            token_url="http://keycloak/token",
            client_id="holmes-client",
        )

        # Register the pending exchange
        _get_exchange_manager().register_pending(
            tool_call_id=tool_call_id,
            code_verifier=code_verifier,
            oauth_config=oauth_config,
        )

        oauth_code = OAuthDecisionCode(
            toolset_name="test-toolset",
            code="auth-code-xyz",
            redirect_uri="http://frontend/callback",
        )

        # Mock the token endpoint response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "final-access-token-abc",
            "token_type": "Bearer",
            "expires_in": 300,
        }
        mock_response.raise_for_status = MagicMock()

        request_context = {"headers": {"X-Conversation-Id": conv_id}}

        with patch("holmes.core.oauth_utils.httpx.post", return_value=mock_response) as mock_post:
            _get_exchange_manager().complete_exchange(tool_call_id, oauth_code, request_context)

            # Verify token endpoint was called correctly
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs[0][0] == "http://keycloak/token"
            post_data = call_kwargs[1]["data"]
            assert post_data["grant_type"] == "authorization_code"
            assert post_data["code"] == "auth-code-xyz"
            assert post_data["client_id"] == "holmes-client"
            assert post_data["code_verifier"] == code_verifier
            assert post_data["redirect_uri"] == "http://frontend/callback"

        # Verify token was cached using the real cache key
        cache_key = _get_token_manager().get_cache_key(oauth_config, request_context)
        assert _get_token_manager().cache.get_valid_access_token(cache_key) == "final-access-token-abc"

        # Pending exchange should be consumed
        assert tool_call_id not in _get_exchange_manager()._pending

    def test_full_flow_stores_to_db_with_metadata(self):
        """After a successful OAuth exchange, _store_to_db is called with token_url and client_id."""
        tool_call_id = "tc-db-store"
        code_verifier = "db-verifier-abc"

        oauth_config = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://idp/authorize",
            token_url="http://idp/token",
            client_id="db-test-client",
        )

        _get_exchange_manager().register_pending(
            tool_call_id=tool_call_id,
            code_verifier=code_verifier,
            oauth_config=oauth_config,
        )

        oauth_code = OAuthDecisionCode(
            toolset_name="db-test-toolset",
            code="db-auth-code",
            redirect_uri="http://frontend/cb",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_success = True
        mock_response.json.return_value = {
            "access_token": "db-access-tok",
            "refresh_token": "db-refresh-tok",
            "expires_in": 1800,
        }

        request_context = {"user_id": "user-db-test"}

        mock_store = MagicMock()
        _get_token_manager()._store = mock_store
        with patch("holmes.core.oauth_utils.httpx.post", return_value=mock_response):
            _get_exchange_manager().complete_exchange(tool_call_id, oauth_code, request_context)

            mock_store.store_token.assert_called_once()
            args, kwargs = mock_store.store_token.call_args
            # Positional: provider_name, token_data
            assert args[0] == "http://idp/authorize"
            assert args[1]["access_token"] == "db-access-tok"
            assert args[1]["refresh_token"] == "db-refresh-tok"
            # Keyword: user_id, token_url, client_id
            assert kwargs["user_id"] == "user-db-test"
            assert kwargs["token_url"] == "http://idp/token"
            assert kwargs["client_id"] == "db-test-client"

    def test_missing_exchange_does_not_crash(self):
        """Gracefully handle missing pending exchange."""
        oauth_code = OAuthDecisionCode(toolset_name="x", code="x", redirect_uri="x")
        _get_exchange_manager().complete_exchange("nonexistent-id", oauth_code, None)
        # Should log error but not raise


class TestParseOAuthDecision:
    def test_valid_oauth_decision(self):
        decision = {
            "toolset_name": "atlassian",
            "code": "auth-code-123",
            "redirect_uri": "http://frontend/callback",
            "client_id": "my-client",
            "code_verifier": "verifier-abc",
        }
        result = parse_oauth_decision(decision)
        assert result is not None
        assert result.code == "auth-code-123"
        assert result.toolset_name == "atlassian"
        assert result.client_id == "my-client"

    def test_minimal_oauth_decision(self):
        decision = {"toolset_name": "mcp", "code": "c", "redirect_uri": "http://x"}
        result = parse_oauth_decision(decision)
        assert result is not None
        assert result.client_id is None

    def test_non_oauth_decision_returns_none(self):
        assert parse_oauth_decision({"some_other": "data"}) is None

    def test_none_returns_none(self):
        assert parse_oauth_decision(None) is None

    def test_empty_dict_returns_none(self):
        assert parse_oauth_decision({}) is None


class TestOAuthCacheKeySharedIdP:
    """Tests that MCP servers sharing the same IdP share the same token."""

    def test_same_idp_same_cache_key(self):
        """Two MCP servers using the same authorization_url + client_id get the same cache key."""
        oauth1 = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://keycloak:8080/realms/mcp/protocol/openid-connect/auth",
            token_url="http://keycloak:8080/realms/mcp/protocol/openid-connect/token",
            client_id="holmes-client",
        )
        oauth2 = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://keycloak:8080/realms/mcp/protocol/openid-connect/auth",
            token_url="http://internal-keycloak:8080/realms/mcp/protocol/openid-connect/token",  # different token_url
            client_id="holmes-client",
        )
        ctx = {"headers": {"X-Conversation-Id": "conv-shared"}}
        key1 = _get_token_manager().get_cache_key(oauth1, ctx)
        key2 = _get_token_manager().get_cache_key(oauth2, ctx)
        assert key1 == key2, "Same authorization_url + client_id should produce same cache key"

    def test_different_idp_different_cache_key(self):
        """Two MCP servers using different IdPs get different cache keys."""
        oauth1 = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://keycloak-a:8080/auth",
            token_url="http://keycloak-a:8080/token",
            client_id="holmes-client",
        )
        oauth2 = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://keycloak-b:8080/auth",
            token_url="http://keycloak-b:8080/token",
            client_id="holmes-client",
        )
        ctx = {"headers": {"X-Conversation-Id": "conv-diff"}}
        key1 = _get_token_manager().get_cache_key(oauth1, ctx)
        key2 = _get_token_manager().get_cache_key(oauth2, ctx)
        assert key1 != key2, "Different authorization_urls should produce different cache keys"

    def test_same_idp_same_cache_key_regardless_of_client_id(self):
        """Same IdP (authorization_url) produces same cache key regardless of client_id.

        Cache key is based on authorization_url + user_id only, so tokens can be
        preloaded from DB without needing toolset config objects.
        """
        oauth1 = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://keycloak:8080/auth",
            token_url="http://keycloak:8080/token",
            client_id="client-a",
        )
        oauth2 = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://keycloak:8080/auth",
            token_url="http://keycloak:8080/token",
            client_id="client-b",
        )
        ctx = {"headers": {"X-Conversation-Id": "conv-cid"}}
        key1 = _get_token_manager().get_cache_key(oauth1, ctx)
        key2 = _get_token_manager().get_cache_key(oauth2, ctx)
        assert key1 == key2, "Same authorization_url should produce same cache key"

    def test_none_authorization_url_does_not_crash(self):
        """OAuth-enabled toolsets with authorization_url=None (pre-discovery) must not crash."""
        oauth = MCPOAuthConfig(
            enabled=True,
            authorization_url=None,
            token_url=None,
            client_id=None,
        )
        ctx = {"headers": {"X-Conversation-Id": "conv-none"}}
        # Should not raise — returns a valid key even with None authorization_url
        key = _get_token_manager().get_cache_key(oauth, ctx)
        assert isinstance(key, str)

        # get_access_token should also not crash
        token = _get_token_manager().get_access_token(oauth, ctx)
        assert token is None

    def test_shared_token_across_mcp_servers(self):
        """Token cached for one MCP server is reusable by another with same IdP."""
        oauth1 = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://keycloak:8080/auth",
            token_url="http://keycloak:8080/token",
            client_id="shared-client",
        )
        oauth2 = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://keycloak:8080/auth",
            token_url="http://internal:8080/token",  # different token_url, same auth
            client_id="shared-client",
        )
        ctx = {"headers": {"X-Conversation-Id": "conv-share-test"}}

        # Cache token via first MCP server's config
        cache_key1 = _get_token_manager().get_cache_key(oauth1, ctx)
        _get_token_manager().cache.set(cache_key1, "shared-token-xyz", expires_in=300)

        # Second MCP server should find the same token
        cache_key2 = _get_token_manager().get_cache_key(oauth2, ctx)
        assert _get_token_manager().cache.get_valid_access_token(cache_key2) == "shared-token-xyz"


@pytest.mark.manual
class TestLiveAtlassianOAuthDiscovery:
    """Live tests against Atlassian's MCP server OAuth discovery.

    These tests hit real Atlassian endpoints to verify our discovery logic
    matches what the MCP SDK does. No authentication is needed — only discovery.

    Run manually: poetry run pytest tests/test_mcp_oauth.py -k "LiveAtlassian" -m manual -v --no-cov
    """

    ATLASSIAN_MCP_URL = "https://mcp.atlassian.com/v1/mcp"

    def test_atlassian_returns_401_on_unauthenticated_request(self):
        """Verify the MCP server returns 401 without a token."""
        response = httpx.post(
            self.ATLASSIAN_MCP_URL,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, "id": 1},
            timeout=15,
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"

    def test_atlassian_prm_not_available(self):
        """Atlassian doesn't serve RFC 9728 Protected Resource Metadata — verify graceful fallback."""
        # Root-based
        r1 = httpx.get("https://mcp.atlassian.com/.well-known/oauth-protected-resource", timeout=10)
        assert r1.status_code != 200, f"Unexpected PRM at root: {r1.status_code}"

        # Path-based
        r2 = httpx.get("https://mcp.atlassian.com/.well-known/oauth-protected-resource/v1/mcp", timeout=10)
        assert r2.status_code != 200, f"Unexpected PRM at path: {r2.status_code}"

    def test_atlassian_legacy_oauth_metadata_available(self):
        """Atlassian serves OAuth metadata at the legacy well-known path on the MCP server."""
        response = httpx.get(
            "https://mcp.atlassian.com/.well-known/oauth-authorization-server",
            timeout=10,
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()

        assert "authorization_endpoint" in data, f"Missing authorization_endpoint. Keys: {list(data.keys())}"
        assert "token_endpoint" in data, f"Missing token_endpoint. Keys: {list(data.keys())}"
        assert "registration_endpoint" in data, f"Missing registration_endpoint (DCR). Keys: {list(data.keys())}"
        assert "authorization_code" in data.get("grant_types_supported", []), f"authorization_code not in grant_types: {data.get('grant_types_supported')}"
        assert "refresh_token" in data.get("grant_types_supported", []), f"refresh_token not in grant_types: {data.get('grant_types_supported')}"

    def test_atlassian_dcr_succeeds(self):
        """Dynamic Client Registration works with Atlassian's auth server."""
        # First get the registration endpoint
        metadata = httpx.get(
            "https://mcp.atlassian.com/.well-known/oauth-authorization-server",
            timeout=10,
        ).json()
        registration_endpoint = metadata["registration_endpoint"]

        # Register a client
        dcr_response = httpx.post(
            registration_endpoint,
            json={
                "client_name": "HolmesGPT OAuth Test",
                "redirect_uris": ["http://127.0.0.1:0/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            timeout=15,
        )
        assert dcr_response.status_code in (200, 201), f"DCR failed: HTTP {dcr_response.status_code} - {dcr_response.text[:300]}"

        dcr_data = dcr_response.json()
        assert "client_id" in dcr_data, f"No client_id in DCR response. Keys: {list(dcr_data.keys())}"
        assert len(dcr_data["client_id"]) > 0, "Empty client_id"

    def test_full_discovery_flow_via_toolset(self):
        """End-to-end: RemoteMCPToolset auto-discovers all OAuth endpoints for Atlassian."""
        toolset = RemoteMCPToolset(name="atlassian-test", enabled=True)
        toolset._mcp_config = MCPConfig(
            url=self.ATLASSIAN_MCP_URL,
            mode=MCPMode.STREAMABLE_HTTP,
            oauth=MCPOAuthConfig(enabled=True),
        )

        # Simulate the initial 401 response
        initial_response = httpx.post(
            self.ATLASSIAN_MCP_URL,
            headers={"Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, "id": 1},
            timeout=15,
        )
        assert initial_response.status_code == 401

        # Run discovery
        result = toolset._discover_oauth_endpoints(self.ATLASSIAN_MCP_URL, initial_response)
        assert result is True, "Discovery should succeed for Atlassian"

        oauth = toolset._mcp_config.oauth
        assert oauth.authorization_url is not None, "authorization_url should be discovered"
        assert oauth.token_url is not None, "token_url should be discovered"
        assert oauth.registration_endpoint is not None, "registration_endpoint should be discovered for deferred DCR"
        # client_id is None because DCR is deferred to runtime (CLI or frontend handles it)
        assert "atlassian" in oauth.authorization_url.lower() or "mcp" in oauth.authorization_url.lower(), f"Unexpected authorization_url: {oauth.authorization_url}"

    def test_full_oauth_flow_with_browser(self):
        """End-to-end: discover endpoints, register client, open browser for user login.

        Run with: poetry run pytest tests/test_mcp_oauth.py -k "test_full_oauth_flow_with_browser" -v --no-cov -s
        """
        # Step 1: Discover OAuth endpoints
        toolset = RemoteMCPToolset(name="atlassian-live", enabled=True)
        toolset._mcp_config = MCPConfig(
            url=self.ATLASSIAN_MCP_URL,
            mode=MCPMode.STREAMABLE_HTTP,
            oauth=MCPOAuthConfig(enabled=True),
        )
        initial_response = httpx.post(
            self.ATLASSIAN_MCP_URL,
            headers={"Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, "id": 1},
            timeout=15,
        )
        result = toolset._discover_oauth_endpoints(self.ATLASSIAN_MCP_URL, initial_response)
        assert result is True, "Discovery failed"

        oauth = toolset._mcp_config.oauth
        print(f"\n  authorization_url: {oauth.authorization_url}")
        print(f"  token_url: {oauth.token_url}")
        print(f"  client_id: {oauth.client_id}")

        # Step 2: Generate PKCE
        code_verifier, code_challenge = generate_pkce()

        # Step 3: Start local callback server (port 0 = OS-assigned)
        auth_code_result = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                if "code" in params:
                    auth_code_result["code"] = params["code"][0]
                    auth_code_result["state"] = params.get("state", [None])[0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<h1>Authenticated! You can close this tab.</h1>")
                else:
                    self.send_response(400)
                    self.end_headers()
                    error = params.get("error", ["unknown"])[0]
                    desc = params.get("error_description", [""])[0]
                    auth_code_result["error"] = f"{error}: {desc}"
                    self.wfile.write(f"<h1>Error: {error} - {desc}</h1>".encode())

            def log_message(self, format, *args):
                pass  # Suppress HTTP logs

        server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
        callback_port = server.server_address[1]
        redirect_uri = f"http://127.0.0.1:{callback_port}/callback"
        print(f"  Callback server on port {callback_port}")

        # Step 3b: Re-register DCR with actual redirect_uri
        if oauth.registration_endpoint:
            dcr_resp = httpx.post(
                oauth.registration_endpoint,
                json={
                    "client_name": "HolmesGPT OAuth Test",
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
                timeout=15,
            )
            if dcr_resp.status_code in (200, 201):
                oauth.client_id = dcr_resp.json().get("client_id", oauth.client_id)
                print(f"  Re-registered client_id={oauth.client_id} with redirect_uri={redirect_uri}")

        # Step 4: Build authorization URL and open browser
        state = secrets.token_urlsafe(32)
        auth_params = {
            "response_type": "code",
            "client_id": oauth.client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if oauth.scopes:
            auth_params["scope"] = " ".join(oauth.scopes)

        auth_url = f"{oauth.authorization_url}?{urlencode(auth_params)}"
        print(f"\n  Opening browser for OAuth login: {auth_url[:100]}...")
        webbrowser.open(auth_url)

        # Step 5: Wait for callback
        print("  Waiting for OAuth callback (login in your browser)...")
        server.handle_request()  # blocks until one request
        server.server_close()

        assert "error" not in auth_code_result, f"OAuth error: {auth_code_result.get('error')}"
        assert "code" in auth_code_result, "No auth code received"
        print(f"  Auth code received: {auth_code_result['code'][:20]}...")

        # Step 6: Exchange code for token
        token_response = httpx.post(
            oauth.token_url,
            data={
                "grant_type": "authorization_code",
                "code": auth_code_result["code"],
                "client_id": oauth.client_id,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        assert token_response.status_code == 200, f"Token exchange failed: HTTP {token_response.status_code} - {token_response.text[:300]}"

        token_data = token_response.json()
        assert "access_token" in token_data, f"No access_token in response. Keys: {list(token_data.keys())}"
        print(f"  Access token obtained: {token_data['access_token'][:30]}...")
        print(f"  Token type: {token_data.get('token_type')}")
        print(f"  Expires in: {token_data.get('expires_in')}s")
        print(f"  Has refresh_token: {'refresh_token' in token_data}")

        # Step 7: Use token to list MCP tools
        async def list_tools():
            headers = {"Authorization": f"Bearer {token_data['access_token']}"}
            async with streamablehttp_client(self.ATLASSIAN_MCP_URL, headers=headers) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    return tools

        tools_result = asyncio.run(list_tools())
        print(f"\n  Discovered {len(tools_result.tools)} tools:")
        for t in tools_result.tools:
            print(f"    - {t.name}: {t.description[:80] if t.description else 'no description'}")

        assert len(tools_result.tools) > 0, "Expected at least one tool from Atlassian MCP server"


class TestCLIOAuthFlow:
    """Tests for the CLI OAuth browser flow with mocked browser/server/network."""

    def _make_oauth_endpoints(self, **overrides):
        defaults = dict(
            authorization_url="http://idp.test/authorize",
            token_url="http://idp.test/token",
            client_id="test-client",
            scopes=["mcp:tools"],
        )
        defaults.update(overrides)
        return OAuthEndpoints(**defaults)

    def test_cli_flow_full_roundtrip(self):
        """Mock browser + callback: DCR → auth URL → callback with code → token exchange."""
        oauth = self._make_oauth_endpoints()

        # Mock httpx.post for the token exchange
        mock_token_response = MagicMock()
        mock_token_response.status_code = 200
        mock_token_response.json.return_value = {
            "access_token": "cli-test-token-abc",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "cli-refresh-xyz",
        }

        def mock_post(url, **kwargs):
            if "token" in url:
                return mock_token_response
            # DCR
            dcr_resp = MagicMock()
            dcr_resp.status_code = 201
            dcr_resp.json.return_value = {"client_id": "dcr-client-123"}
            return dcr_resp

        # Mock webbrowser.open to simulate the callback instead
        def mock_browser_open(auth_url):
            """Parse the auth URL, extract state, and POST back to the callback server."""
            parsed = urlparse(auth_url)
            params = parse_qs(parsed.query)
            state = params["state"][0]
            redirect_uri = params["redirect_uri"][0]
            redirect_parsed = urlparse(redirect_uri)
            port = redirect_parsed.port

            # Simulate IdP redirecting back with an auth code
            def send_callback():
                time.sleep(0.3)  # Give server time to start
                callback_url = f"http://127.0.0.1:{port}/callback?code=mock-auth-code-999&state={state}"
                try:
                    urllib.request.urlopen(callback_url, timeout=5)
                except Exception:
                    pass  # Response doesn't matter

            threading.Thread(target=send_callback, daemon=True).start()

        with patch("holmes.core.oauth_utils.httpx.post", side_effect=mock_post), \
             patch("holmes.core.oauth_utils.webbrowser.open", side_effect=mock_browser_open):
            result = cli_oauth_flow(oauth, "test-server")

        assert result is not None, "CLI flow should return token data"
        assert result["access_token"] == "cli-test-token-abc"
        assert result["refresh_token"] == "cli-refresh-xyz"
        assert result["expires_in"] == 3600
        assert "expires_at" in result, "Should add expires_at for disk storage"

    def test_cli_flow_with_dcr(self):
        """CLI flow performs DCR when client_id is None."""

        oauth = self._make_oauth_endpoints(client_id=None, registration_endpoint="http://idp.test/register")

        def mock_post(url, **kwargs):
            if "register" in url:
                resp = MagicMock()
                resp.status_code = 201
                resp.json.return_value = {"client_id": "dcr-new-client"}
                return resp
            if "token" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"access_token": "dcr-token", "expires_in": 300}
                return resp
            raise ValueError(f"Unexpected URL: {url}")

        def mock_browser_open(auth_url):
            parsed = urlparse(auth_url)
            params = parse_qs(parsed.query)
            state = params["state"][0]
            redirect_uri = params["redirect_uri"][0]
            port = urlparse(redirect_uri).port

            def send_callback():
                time.sleep(0.3)
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=dcr-code&state={state}", timeout=5)
                except Exception:
                    pass

            threading.Thread(target=send_callback, daemon=True).start()

        with patch("holmes.core.oauth_utils.httpx.post", side_effect=mock_post), \
             patch("holmes.core.oauth_utils.webbrowser.open", side_effect=mock_browser_open):
            result = cli_oauth_flow(oauth, "dcr-test")

        assert result is not None
        assert result["access_token"] == "dcr-token"
        assert oauth.client_id == "dcr-new-client", "DCR should set client_id on the config"

    def test_cli_flow_dcr_sets_client_id(self):
        """After DCR, client_id is set on the config. Cache key is based on
        authorization_url (stable across DCR), so it stays the same."""

        oauth = self._make_oauth_endpoints(client_id=None, registration_endpoint="http://idp.test/register")
        ctx = {"headers": {"X-Conversation-Id": "cli-conv"}}

        # Cache key before DCR (client_id=None)
        key_before = _get_token_manager().get_cache_key(oauth, ctx)

        def mock_post(url, **kwargs):
            if "register" in url:
                resp = MagicMock()
                resp.status_code = 201
                resp.json.return_value = {"client_id": "new-dcr-id"}
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"access_token": "tok", "expires_in": 300}
            return resp

        def mock_browser_open(auth_url):
            parsed = urlparse(auth_url)
            params = parse_qs(parsed.query)
            state = params["state"][0]
            port = urlparse(params["redirect_uri"][0]).port

            def send_callback():
                time.sleep(0.3)
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=c&state={state}", timeout=5)
                except Exception:
                    pass

            threading.Thread(target=send_callback, daemon=True).start()

        with patch("holmes.core.oauth_utils.httpx.post", side_effect=mock_post), \
             patch("holmes.core.oauth_utils.webbrowser.open", side_effect=mock_browser_open):
            cli_oauth_flow(oauth, "key-test")

        # Cache key after DCR — same because it's based on authorization_url, not client_id
        key_after = _get_token_manager().get_cache_key(oauth, ctx)

        assert key_before == key_after, "Cache key should be stable (based on authorization_url)"
        assert oauth.client_id == "new-dcr-id"

    def test_cli_flow_fails_without_endpoints(self):
        """CLI flow returns None when authorization_url or token_url is missing."""
        oauth = OAuthEndpoints(authorization_url=None, token_url=None, client_id="x")
        result = cli_oauth_flow(oauth, "no-endpoints")
        assert result is None

    def test_cli_flow_fails_without_client_id_and_no_dcr(self):
        """CLI flow returns None when client_id is None and no registration_endpoint."""
        oauth = OAuthEndpoints(
            authorization_url="http://idp/auth",
            token_url="http://idp/token",
            client_id=None,
            registration_endpoint=None,
        )
        result = cli_oauth_flow(oauth, "no-dcr")
        assert result is None

    def test_cli_flow_token_exchange_failure(self):
        """CLI flow returns None when token exchange fails."""

        oauth = self._make_oauth_endpoints()

        def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 401
            resp.text = "invalid_grant"
            return resp

        def mock_browser_open(auth_url):
            parsed = urlparse(auth_url)
            params = parse_qs(parsed.query)
            state = params["state"][0]
            port = urlparse(params["redirect_uri"][0]).port

            def send_callback():
                time.sleep(0.3)
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=bad&state={state}", timeout=5)
                except Exception:
                    pass

            threading.Thread(target=send_callback, daemon=True).start()

        with patch("holmes.core.oauth_utils.httpx.post", side_effect=mock_post), \
             patch("holmes.core.oauth_utils.webbrowser.open", side_effect=mock_browser_open):
            result = cli_oauth_flow(oauth, "fail-test")

        assert result is None


# ---------------------------------------------------------------------------
# MCPOAuthConfig auto_enable validator
# ---------------------------------------------------------------------------
class TestMCPOAuthConfigAutoEnable:
    def test_auto_enables_when_authorization_url_set(self):
        oauth = MCPOAuthConfig(authorization_url="http://auth/authorize")
        assert oauth.enabled is True

    def test_auto_enables_when_token_url_set(self):
        oauth = MCPOAuthConfig(token_url="http://auth/token")
        assert oauth.enabled is True

    def test_auto_enables_when_client_id_set(self):
        oauth = MCPOAuthConfig(client_id="my-client")
        assert oauth.enabled is True

    def test_stays_disabled_when_nothing_set(self):
        oauth = MCPOAuthConfig()
        assert oauth.enabled is False

    def test_explicit_enabled_false_overridden_by_fields(self):
        oauth = MCPOAuthConfig(enabled=False, client_id="cid")
        assert oauth.enabled is True


# ---------------------------------------------------------------------------
# _get_user_id
# ---------------------------------------------------------------------------
class TestGetUserId:
    def test_returns_user_id(self):
        ctx = {"user_id": "user-42"}
        assert _get_user_id(ctx) == "user-42"

    def test_returns_none_when_missing(self):
        assert _get_user_id({}) is None

    def test_returns_none_when_context_is_none(self):
        assert _get_user_id(None) is None


# ---------------------------------------------------------------------------
# OAuthTokenCache — refresh token support
# ---------------------------------------------------------------------------
class TestOAuthTokenCacheRefresh:
    def test_get_refresh_token_valid(self):
        cache = OAuthTokenCache()
        cache.set("k", "access", expires_in=60, refresh_token="refresh-tok", refresh_expires_in=3600)
        assert cache.get_refresh_token("k") == "refresh-tok"

    def test_get_refresh_token_expired_still_returned(self):
        """Expired refresh tokens are still returned — the IdP decides if they're valid."""
        cache = OAuthTokenCache()
        cache.set("k", "access", expires_in=60, refresh_token="r", refresh_expires_in=3600)
        cache._cache["k"].refresh_expires_at = time.monotonic() - 1
        assert cache.get_refresh_token("k") == "r"

    def test_get_refresh_token_missing(self):
        cache = OAuthTokenCache()
        assert cache.get_refresh_token("nonexistent") is None

    def test_has_true_when_access_expired_but_refresh_valid(self):
        cache = OAuthTokenCache()
        cache.set("k", "access", expires_in=60, refresh_token="r", refresh_expires_in=3600)
        cache._cache["k"].expires_at = time.monotonic() - 1
        assert cache.has_token_or_refresh("k") is True

    def test_get_returns_none_when_access_expired_refresh_valid(self):
        """get() should return None when access is expired, even if refresh is valid — caller must refresh."""
        cache = OAuthTokenCache()
        cache.set("k", "access", expires_in=60, refresh_token="r", refresh_expires_in=3600)
        cache._cache["k"].expires_at = time.monotonic() - 1
        assert cache.get_valid_access_token("k") is None

    def test_both_expired_still_has_if_refresh_token_present(self):
        """Entry kept even when both expired — refresh token may still work at the IdP."""
        cache = OAuthTokenCache()
        cache.set("k", "access", expires_in=60, refresh_token="r", refresh_expires_in=3600)
        cache._cache["k"].expires_at = time.monotonic() - 1
        cache._cache["k"].refresh_expires_at = time.monotonic() - 1
        assert cache.has_token_or_refresh("k") is True  # kept because refresh token exists

    def test_no_refresh_token_evicts_entry(self):
        """Entry without refresh token is evicted when access expires."""
        cache = OAuthTokenCache()
        cache.set("k", "access", expires_in=60)
        cache._cache["k"].expires_at = time.monotonic() - 1
        assert cache.has_token_or_refresh("k") is False
        assert "k" not in cache._cache

    def test_set_without_refresh_token(self):
        cache = OAuthTokenCache()
        cache.set("k", "access", expires_in=60)
        entry = cache._cache["k"]
        assert entry.refresh_token is None
        assert entry.refresh_expires_at is None
        assert entry.refresh_expired is True


# ---------------------------------------------------------------------------
# _CachedToken
# ---------------------------------------------------------------------------
class TestCachedToken:
    def test_access_not_expired(self):
        t = _CachedToken("tok", time.monotonic() + 100)
        assert not t.access_expired

    def test_access_expired(self):
        t = _CachedToken("tok", time.monotonic() - 1)
        assert t.access_expired

    def test_refresh_expired_when_no_refresh(self):
        t = _CachedToken("tok", time.monotonic() + 100)
        assert t.refresh_expired

    def test_refresh_not_expired(self):
        t = _CachedToken("tok", time.monotonic() + 100, "rtok", time.monotonic() + 1000)
        assert not t.refresh_expired


# ---------------------------------------------------------------------------
# DiskTokenStore
# ---------------------------------------------------------------------------
class TestDiskTokenStore:
    def test_set_and_get(self, tmp_path):
        store = DiskTokenStore.__new__(DiskTokenStore)
        store._path = tmp_path / "auth" / "mcp_tokens.json"
        store._enabled = True
        store._lock = threading.Lock()
        store._path.parent.mkdir(parents=True, exist_ok=True)

        token_data = {"access_token": "abc", "expires_at": time.time() + 3600}
        store.store_token("server-1", token_data)

        result = store.get_token("server-1")
        assert result is not None
        assert result["access_token"] == "abc"

    def test_get_returns_none_when_expired(self, tmp_path):
        store = DiskTokenStore.__new__(DiskTokenStore)
        store._path = tmp_path / "auth" / "mcp_tokens.json"
        store._enabled = True
        store._lock = threading.Lock()
        store._path.parent.mkdir(parents=True, exist_ok=True)

        token_data = {"access_token": "old", "expires_at": time.time() - 10}
        store.store_token("expired", token_data)
        assert store.get_token("expired") is None

    def test_disabled_store(self, tmp_path):
        store = DiskTokenStore.__new__(DiskTokenStore)
        store._path = tmp_path / "auth" / "mcp_tokens.json"
        store._enabled = False
        store._lock = threading.Lock()

        store.store_token("k", {"access_token": "t"})
        assert store.get_token("k") is None

    def test_corrupted_file(self, tmp_path):
        store = DiskTokenStore.__new__(DiskTokenStore)
        store._path = tmp_path / "auth" / "mcp_tokens.json"
        store._enabled = True
        store._lock = threading.Lock()
        store._path.parent.mkdir(parents=True, exist_ok=True)
        store._path.write_text("not valid json{{{")

        assert store.get_token("k") is None


# ---------------------------------------------------------------------------
# DB token encryption / decryption
# ---------------------------------------------------------------------------
class TestDBTokenEncryption:
    @patch("holmes.config.Config.get_robusta_global_config_value", return_value="test-signing-key-for-encryption")
    def test_roundtrip(self, _mock):
        from holmes.plugins.toolsets.mcp.oauth_token_store import DalTokenStore
        store = DalTokenStore(dal=MagicMock())

        token_data = {"access_token": "abc123", "refresh_token": "ref456", "expires_in": 300}
        encrypted = store._encrypt_token(token_data)
        assert encrypted is not None
        assert encrypted != json.dumps(token_data)

        decrypted = store._decrypt_token(encrypted)
        assert decrypted == token_data

    def test_wrong_signing_key_returns_none(self):
        from holmes.plugins.toolsets.mcp.oauth_token_store import DalTokenStore
        store1 = DalTokenStore(dal=MagicMock())
        with patch("holmes.config.Config.get_robusta_global_config_value", return_value="correct-key"):
            token_data = {"access_token": "secret"}
            encrypted = store1._encrypt_token(token_data)

        store2 = DalTokenStore(dal=MagicMock())
        with patch("holmes.config.Config.get_robusta_global_config_value", return_value="wrong-key"):
            result = store2._decrypt_token(encrypted)
        assert result is None

    @patch("holmes.config.Config.get_robusta_global_config_value", return_value="some-key")
    def test_garbage_input_returns_none(self, _mock):
        from holmes.plugins.toolsets.mcp.oauth_token_store import DalTokenStore
        store = DalTokenStore(dal=MagicMock())
        result = store._decrypt_token("not-valid-fernet-ciphertext")
        assert result is None


# ---------------------------------------------------------------------------
# _render_headers (OAuth token injection)
# ---------------------------------------------------------------------------
class TestRenderHeadersOAuth:
    def _make_toolset(self, oauth_config=None):
        ts = RemoteMCPToolset(name="inject-test", enabled=True)
        ts._mcp_config = MCPConfig(
            url="http://mcp:8000",
            mode=MCPMode.STREAMABLE_HTTP,
            oauth=oauth_config,
        )
        return ts

    def test_injects_bearer_when_cached(self):
        oauth = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://idp/auth",
            token_url="http://idp/token",
            client_id="inject-cid",
        )
        ts = self._make_toolset(oauth)
        ctx = {"headers": {"X-Conversation-Id": "inject-conv"}}
        cache_key = _get_token_manager().get_cache_key(oauth, ctx)
        _get_token_manager().cache.set(cache_key, "my-bearer-token", expires_in=300)

        result = ts._render_headers(ctx)
        assert result["Authorization"] == "Bearer my-bearer-token"

    def test_no_injection_without_oauth(self):
        ts = self._make_toolset(oauth_config=None)
        result = ts._render_headers(None)
        assert result is None or "Authorization" not in (result or {})

    def test_no_injection_when_no_cached_token(self):
        oauth = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://idp-no-cache/auth",
            token_url="http://idp-no-cache/token",
            client_id="no-cache-cid",
        )
        ts = self._make_toolset(oauth)
        ctx = {"headers": {"X-Conversation-Id": "no-cache-conv"}}

        result = ts._render_headers(ctx)

        assert result is None or "Authorization" not in (result or {})

    def test_triggers_refresh_on_expired_access(self):
        oauth = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://idp-refresh/auth",
            token_url="http://idp-refresh/token",
            client_id="refresh-inject-cid",
        )
        ts = self._make_toolset(oauth)
        ctx = {"headers": {"X-Conversation-Id": "refresh-inject-conv"}}
        cache_key = _get_token_manager().get_cache_key(oauth, ctx)

        _get_token_manager().cache.set(cache_key, "old", expires_in=60, refresh_token="r", refresh_expires_in=3600)
        _get_token_manager().cache._cache[cache_key].expires_at = time.monotonic() - 1

        with patch.object(
            type(_get_token_manager()), "_refresh_token",
            return_value="refreshed-tok",
        ) as mock_refresh:
            result = ts._render_headers(ctx)

        mock_refresh.assert_called_once()
        assert result is not None
        assert result["Authorization"] == "Bearer refreshed-tok"


# ---------------------------------------------------------------------------
# _discover_oauth_endpoints (mocked HTTP)
# ---------------------------------------------------------------------------
class TestDiscoverOAuthEndpoints:
    def _make_toolset_for_discovery(self):
        ts = RemoteMCPToolset(name="discover-test", enabled=True)
        ts._mcp_config = MCPConfig(
            url="http://mcp-server:8000/v1/mcp",
            mode=MCPMode.STREAMABLE_HTTP,
            oauth=MCPOAuthConfig(enabled=True),
        )
        return ts

    def _mock_401_response(self, www_authenticate=""):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 401
        resp.headers = httpx.Headers({"www-authenticate": www_authenticate})
        return resp

    def test_discovery_via_legacy_fallback(self):
        """When PRM returns 404, falls back to /.well-known/oauth-authorization-server."""
        ts = self._make_toolset_for_discovery()
        initial_resp = self._mock_401_response()

        def mock_get(url, **kwargs):
            resp = MagicMock()
            if "oauth-protected-resource" in url:
                resp.status_code = 404
                return resp
            if "oauth-authorization-server" in url:
                resp.status_code = 200
                resp.json.return_value = {
                    "authorization_endpoint": "http://idp/authorize",
                    "token_endpoint": "http://idp/token",
                    "registration_endpoint": "http://idp/register",
                }
                return resp
            resp.status_code = 404
            return resp

        with patch("holmes.core.oauth_utils.httpx.get", side_effect=mock_get):
            result = ts._discover_oauth_endpoints("http://mcp-server:8000/v1/mcp", initial_resp)

        assert result is True
        assert ts._mcp_config.oauth.authorization_url == "http://idp/authorize"
        assert ts._mcp_config.oauth.token_url == "http://idp/token"
        assert ts._mcp_config.oauth.registration_endpoint == "http://idp/register"

    def test_discovery_via_prm(self):
        """PRM returns auth server, then fetches OIDC metadata from that server."""
        ts = self._make_toolset_for_discovery()
        initial_resp = self._mock_401_response()

        def mock_get(url, **kwargs):
            resp = MagicMock()
            if "oauth-protected-resource" in url:
                resp.status_code = 200
                resp.json.return_value = {
                    "authorization_servers": ["http://auth-server.example.com/realm"],
                    "scopes_supported": ["mcp:tools"],
                }
                return resp
            if "auth-server.example.com" in url and "oauth-authorization-server" in url:
                resp.status_code = 200
                resp.json.return_value = {
                    "authorization_endpoint": "http://auth-server.example.com/realm/authorize",
                    "token_endpoint": "http://auth-server.example.com/realm/token",
                }
                return resp
            resp.status_code = 404
            return resp

        with patch("holmes.core.oauth_utils.httpx.get", side_effect=mock_get):
            result = ts._discover_oauth_endpoints("http://mcp-server:8000/v1/mcp", initial_resp)

        assert result is True
        assert ts._mcp_config.oauth.authorization_url == "http://auth-server.example.com/realm/authorize"
        assert ts._mcp_config.oauth.token_url == "http://auth-server.example.com/realm/token"
        assert ts._mcp_config.oauth.scopes == ["mcp:tools"]

    def test_discovery_via_www_authenticate_header(self):
        """resource_metadata URL from WWW-Authenticate header is tried first."""
        ts = self._make_toolset_for_discovery()
        initial_resp = self._mock_401_response(
            www_authenticate='Bearer resource_metadata="http://custom-prm/metadata"'
        )

        call_urls = []

        def mock_get(url, **kwargs):
            call_urls.append(url)
            resp = MagicMock()
            if url == "http://custom-prm/metadata":
                resp.status_code = 200
                resp.json.return_value = {
                    "authorization_servers": ["http://custom-auth"],
                }
                return resp
            if "custom-auth" in url and "oauth-authorization-server" in url:
                resp.status_code = 200
                resp.json.return_value = {
                    "authorization_endpoint": "http://custom-auth/authorize",
                    "token_endpoint": "http://custom-auth/token",
                }
                return resp
            resp.status_code = 404
            return resp

        with patch("holmes.core.oauth_utils.httpx.get", side_effect=mock_get):
            result = ts._discover_oauth_endpoints("http://mcp-server:8000/v1/mcp", initial_resp)

        assert result is True
        assert call_urls[0] == "http://custom-prm/metadata"

    def test_discovery_fails_when_no_metadata(self):
        """Returns False when all discovery attempts fail."""
        ts = self._make_toolset_for_discovery()
        initial_resp = self._mock_401_response()

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 404
            return resp

        with patch("holmes.core.oauth_utils.httpx.get", side_effect=mock_get):
            result = ts._discover_oauth_endpoints("http://mcp-server:8000/v1/mcp", initial_resp)

        assert result is False

    def test_discovery_preserves_existing_config(self):
        """If authorization_url is already set, discovery doesn't overwrite it."""
        ts = self._make_toolset_for_discovery()
        ts._mcp_config.oauth.authorization_url = "http://manual/authorize"
        initial_resp = self._mock_401_response()

        def mock_get(url, **kwargs):
            resp = MagicMock()
            if "oauth-authorization-server" in url:
                resp.status_code = 200
                resp.json.return_value = {
                    "authorization_endpoint": "http://discovered/authorize",
                    "token_endpoint": "http://discovered/token",
                }
                return resp
            resp.status_code = 404
            return resp

        with patch("holmes.core.oauth_utils.httpx.get", side_effect=mock_get):
            result = ts._discover_oauth_endpoints("http://mcp-server:8000/v1/mcp", initial_resp)

        assert result is True
        assert ts._mcp_config.oauth.authorization_url == "http://manual/authorize"
        assert ts._mcp_config.oauth.token_url == "http://discovered/token"


# ---------------------------------------------------------------------------
# ToolExecutor — dynamic tools and prefix stripping
# ---------------------------------------------------------------------------
class TestToolExecutorDynamicTools:
    """Tests for ToolExecutor._sync_dynamic_tools and prefix-stripping lookup."""

    def _make_tool(self, name: str, description: str = "test tool"):
        class FakeTool(Tool):
            def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
                return StructuredToolResult(status="success", data="ok", params=params, invocation=self.name)

            def get_parameterized_one_liner(self, params: dict) -> str:
                return f"{self.name}({params})"

        return FakeTool(name=name, description=description, parameters={})

    def _make_toolset(self, name: str, tools: list, mcp: bool = False):
        if mcp:
            ts = RemoteMCPToolset(name=name, description="test", enabled=True, tools=tools, tags=[ToolsetTag.CORE])
        else:
            class FakeToolset(Toolset):
                model_config = ConfigDict(extra="forbid")
            ts = FakeToolset(name=name, description="test", enabled=True, tools=tools, tags=[ToolsetTag.CORE])
        ts.status = ToolsetStatusEnum.ENABLED
        return ts

    def test_unknown_tool_returns_none(self):
        tool = self._make_tool("some_tool")
        ts = self._make_toolset("ts", [tool])
        executor = ToolExecutor([ts])

        assert executor.get_tool_by_name("nonexistent") is None

    def test_store_and_retrieve_user_oauth_tools(self):
        """Tools stored per-user are returned by get_all_tools_openai_format and get_tool_by_name."""
        placeholder = self._make_tool("my-mcp_connect")
        ts = self._make_toolset("my-mcp", [], mcp=True)
        ts.tools = [placeholder]
        executor = ToolExecutor([ts])

        # Before: placeholder visible, real tools not
        assert "my-mcp_connect" in executor.tools_by_name
        tools_before = executor.get_all_tools_openai_format(user_id="user-1")
        tool_names_before = {t["function"]["name"] for t in tools_before}
        assert "my-mcp_connect" in tool_names_before
        assert "real_tool_a" not in tool_names_before

        # Store real tools for user-1
        real_a = self._make_tool("real_tool_a")
        real_b = self._make_tool("real_tool_b")
        executor.oauth_connector.store_user_tools("user-1", "my-mcp", [real_a, real_b])

        # After: user-1 sees real tools, placeholder is hidden
        tools_after = executor.get_all_tools_openai_format(user_id="user-1")
        tool_names_after = {t["function"]["name"] for t in tools_after}
        assert "real_tool_a" in tool_names_after
        assert "real_tool_b" in tool_names_after
        assert "my-mcp_connect" not in tool_names_after

        # get_tool_by_name finds user tools
        assert executor.get_tool_by_name("real_tool_a", user_id="user-1") is real_a
        assert executor.get_tool_by_name("real_tool_a", user_id="user-2") is None

    def test_user_tools_dont_affect_other_users(self):
        """One user's OAuth tools don't leak to another user."""
        placeholder = self._make_tool("mcp_connect")
        ts = self._make_toolset("mcp", [], mcp=True)
        ts.tools = [placeholder]
        executor = ToolExecutor([ts])

        real = self._make_tool("secret_tool")
        executor.oauth_connector.store_user_tools("user-A", "mcp", [real])

        # user-A sees real tools
        tools_a = executor.get_all_tools_openai_format(user_id="user-A")
        assert "secret_tool" in {t["function"]["name"] for t in tools_a}
        assert "mcp_connect" not in {t["function"]["name"] for t in tools_a}

        # user-B still sees placeholder
        tools_b = executor.get_all_tools_openai_format(user_id="user-B")
        assert "mcp_connect" in {t["function"]["name"] for t in tools_b}
        assert "secret_tool" not in {t["function"]["name"] for t in tools_b}

        # No user_id sees placeholder (base tools)
        tools_none = executor.get_all_tools_openai_format(user_id=None)
        assert "mcp_connect" in {t["function"]["name"] for t in tools_none}
        assert "secret_tool" not in {t["function"]["name"] for t in tools_none}

    def test_clone_shares_user_tools(self):
        """Cloned executor shares the per-user tools store."""
        placeholder = self._make_tool("mcp_connect")
        ts = self._make_toolset("mcp", [], mcp=True)
        ts.tools = [placeholder]
        executor = ToolExecutor([ts])

        real = self._make_tool("cloned_tool")
        executor.oauth_connector.store_user_tools("user-X", "mcp", [real])

        clone = executor.clone_with_extra_tools([])
        # Clone sees user tools
        tools = clone.get_all_tools_openai_format(user_id="user-X")
        assert "cloned_tool" in {t["function"]["name"] for t in tools}

        # Storing on clone also visible on original (shared reference)
        real2 = self._make_tool("another_tool")
        clone.oauth_connector.store_user_tools("user-Y", "mcp", [real2])
        assert executor.get_tool_by_name("another_tool", user_id="user-Y") is real2

    def test_non_oauth_toolsets_unaffected(self):
        """Non-OAuth toolsets are never filtered out by user tool replacement."""
        regular_tool = self._make_tool("kubectl_get")
        regular_ts = self._make_toolset("kubernetes", [regular_tool])
        placeholder = self._make_tool("mcp_connect")
        mcp_ts = self._make_toolset("mcp", [], mcp=True)
        mcp_ts.tools = [placeholder]
        executor = ToolExecutor([regular_ts, mcp_ts])

        real = self._make_tool("mcp_real")
        executor.oauth_connector.store_user_tools("user-1", "mcp", [real])

        tools = executor.get_all_tools_openai_format(user_id="user-1")
        tool_names = {t["function"]["name"] for t in tools}
        # Regular toolset tool preserved
        assert "kubectl_get" in tool_names
        # OAuth replacement applied
        assert "mcp_real" in tool_names
        assert "mcp_connect" not in tool_names


    def test_get_toolset_name_works_for_oauth_tools(self):
        """get_toolset_name returns correct name for per-user OAuth tools via _tool_to_toolset."""
        mcp_ts = self._make_toolset("atlassian", [], mcp=True)
        placeholder = self._make_tool("atlassian_connect")
        mcp_ts.tools = [placeholder]
        executor = ToolExecutor([mcp_ts])

        # Before storing: placeholder is registered
        assert executor.get_toolset_name("atlassian_connect") == "atlassian"
        assert executor.get_toolset_name("searchConfluence", user_id="user-1") is None

        # Create a RemoteMCPTool with proper .toolset reference
        real = RemoteMCPTool(
            name="searchConfluence",
            description="Search Confluence",
            parameters={},
            toolset=mcp_ts,
        )
        executor.oauth_connector.store_user_tools("user-1", "atlassian", [real])

        # After storing: found with user_id
        assert executor.get_toolset_name("searchConfluence", user_id="user-1") == "atlassian"
        # Not found for wrong user
        assert executor.get_toolset_name("searchConfluence", user_id="user-2") is None
        # Not found without user_id
        assert executor.get_toolset_name("searchConfluence") is None

    def test_get_tool_by_name_finds_oauth_tools(self):
        """get_tool_by_name finds per-user OAuth tools with user_id."""
        mcp_ts = self._make_toolset("notion", [], mcp=True)
        placeholder = self._make_tool("notion_connect")
        mcp_ts.tools = [placeholder]
        executor = ToolExecutor([mcp_ts])

        real = RemoteMCPTool(
            name="searchPages",
            description="Search pages",
            parameters={},
            toolset=mcp_ts,
        )
        executor.oauth_connector.store_user_tools("user-A", "notion", [real])

        # Found with correct user_id
        assert executor.get_tool_by_name("searchPages", user_id="user-A") is real
        # Not found without user_id (per-user only)
        assert executor.get_tool_by_name("searchPages") is None
        # Not found for wrong user
        assert executor.get_tool_by_name("searchPages", user_id="user-B") is None
        # Placeholder still found for everyone
        assert executor.get_tool_by_name("notion_connect") is placeholder

    def test_oauth_tools_have_correct_parameterized_one_liner(self):
        """OAuth RemoteMCPTools produce a description via get_parameterized_one_liner."""
        mcp_ts = self._make_toolset("atlassian", [], mcp=True)
        mcp_ts.tools = []
        executor = ToolExecutor([mcp_ts])

        real = RemoteMCPTool(
            name="searchConfluenceUsingCql",
            description="Search Confluence using CQL",
            parameters={},
            toolset=mcp_ts,
        )
        executor.oauth_connector.store_user_tools("user-1", "atlassian", [real])

        tool = executor.get_tool_by_name("searchConfluenceUsingCql", user_id="user-1")
        one_liner = tool.get_parameterized_one_liner({"cql": "type=page"})
        assert "atlassian" in one_liner
        assert "searchConfluenceUsingCql" in one_liner


# ---------------------------------------------------------------------------
# _invoke_oauth_connect returns oauth_tools
# ---------------------------------------------------------------------------
class TestInvokeOAuthConnectReturnsTools:
    """Verify _invoke_oauth_connect returns real tools via StructuredToolResult.oauth_tools."""

    def test_connect_returns_oauth_tools_in_result(self):
        """_invoke_oauth_connect populates oauth_tools on the result."""
        from unittest.mock import AsyncMock

        ts = RemoteMCPToolset(name="test-mcp", enabled=True)
        ts._mcp_config = MCPConfig(
            url="http://mcp:8000",
            mode=MCPMode.STREAMABLE_HTTP,
            oauth=MCPOAuthConfig(
                enabled=True,
                authorization_url="http://idp/auth",
                token_url="http://idp/token",
                client_id="cid",
            ),
        )
        # Create a placeholder connect tool
        from mcp.types import Tool as MCP_Tool
        placeholder = MCP_Tool(
            name=ts.connect_tool_name,
            description="connect",
            inputSchema={"type": "object", "properties": {}},
        )
        connect_tool = RemoteMCPTool.create(placeholder, ts)

        # Mock the MCP server returning real tools
        mock_tools_result = MagicMock()
        mock_real_tool = MagicMock()
        mock_real_tool.name = "real_add"
        mock_real_tool.description = "Add numbers"
        mock_real_tool.inputSchema = {"type": "object", "properties": {"a": {"type": "number"}}}
        mock_tools_result.tools = [mock_real_tool]

        invoke_context = ToolInvokeContext.model_construct(
            tool_call_id="tc-1",
            tool_name=ts.connect_tool_name,
            llm=MagicMock(),
            max_token_count=1000,
            request_context={"user_id": "user-connect-test"},
        )

        with patch("holmes.plugins.toolsets.mcp.toolset_mcp.asyncio") as mock_asyncio, \
             patch("holmes.plugins.toolsets.mcp.toolset_mcp.get_server_lock", return_value=MagicMock()):
            mock_asyncio.run.return_value = mock_tools_result
            result = connect_tool._invoke_oauth_connect({}, invoke_context)

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.oauth_tools is not None
        assert len(result.oauth_tools) == 1
        assert result.oauth_tools[0].name == "real_add"

    def test_connect_failure_returns_no_oauth_tools(self):
        """On failure, oauth_tools is None."""
        ts = RemoteMCPToolset(name="fail-mcp", enabled=True)
        ts._mcp_config = MCPConfig(
            url="http://mcp:8000",
            mode=MCPMode.STREAMABLE_HTTP,
            oauth=MCPOAuthConfig(enabled=True, authorization_url="http://idp/auth", token_url="http://idp/token", client_id="c"),
        )
        from mcp.types import Tool as MCP_Tool
        placeholder = MCP_Tool(name=ts.connect_tool_name, description="connect", inputSchema={"type": "object", "properties": {}})
        connect_tool = RemoteMCPTool.create(placeholder, ts)

        invoke_context = ToolInvokeContext.model_construct(
            tool_call_id="tc-2",
            tool_name=ts.connect_tool_name,
            llm=MagicMock(),
            max_token_count=1000,
            request_context={"user_id": "user-fail"},
        )

        with patch("holmes.plugins.toolsets.mcp.toolset_mcp.asyncio") as mock_asyncio, \
             patch("holmes.plugins.toolsets.mcp.toolset_mcp.get_server_lock", return_value=MagicMock()):
            mock_asyncio.run.side_effect = ConnectionError("MCP down")
            result = connect_tool._invoke_oauth_connect({}, invoke_context)

        assert result.status == StructuredToolResultStatus.ERROR
        assert result.oauth_tools is None

    def test_llm_layer_stores_oauth_tools_from_result(self):
        """_directly_invoke_tool_call stores oauth_tools returned by connect on the executor."""
        placeholder = MagicMock()
        placeholder.name = "mcp_connect"
        placeholder._is_restricted.return_value = False
        placeholder.get_openai_format.return_value = {"function": {"name": "mcp_connect"}}

        real_tool = MagicMock()
        real_tool.name = "real_tool"

        # Tool returns oauth_tools in result
        tool_result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="Connected",
            params={},
            oauth_tools=[real_tool],
        )
        placeholder.invoke.return_value = tool_result

        mcp_ts = RemoteMCPToolset(name="mcp", enabled=True)
        mcp_ts.status = ToolsetStatusEnum.ENABLED
        mcp_ts.tools = [placeholder]
        executor = ToolExecutor([mcp_ts])

        from holmes.core.tool_calling_llm import ToolCallingLLM
        from holmes.core.llm import LLM
        llm_mock = MagicMock(spec=LLM)
        llm_mock.get_max_token_count_for_single_tool.return_value = 1000
        tcl = ToolCallingLLM(
            tool_executor=executor,
            max_steps=10,
            llm=llm_mock,
            tool_results_dir=None,
        )

        result = tcl._directly_invoke_tool_call(
            tool_name="mcp_connect",
            tool_params={},
            user_approved=True,
            tool_call_id="tc-store",
            request_context={"user_id": "user-store-test"},
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        # Verify tools were stored per user
        stored = executor.oauth_connector.resolve_tools("user-store-test")
        assert stored is not None
        assert "mcp" in stored
        assert stored["mcp"][0].name == "real_tool"


# ---------------------------------------------------------------------------
# Background sweep
# ---------------------------------------------------------------------------
class TestBackgroundSweep:
    def _make_manager(self) -> OAuthTokenManager:
        manager = OAuthTokenManager()
        manager._shutdown_event.set()  # prevent background thread from running
        mock_store = MagicMock()
        mock_store.get_token.return_value = None
        mock_store.get_all_for_preload.return_value = []
        manager._store = mock_store
        return manager

    def test_sweep_refreshes_expiring_token_with_refresh_token(self):
        """Tokens with refresh tokens are refreshed and pushed to DB."""
        manager = self._make_manager()
        cache_key = "user1:__no_conv__:http://idp/auth:cid1"

        manager._cache.set(
            cache_key, "old-access", expires_in=600,
            refresh_token="refresh-tok",
            token_url="http://idp/token", client_id="cid1",
            authorization_url="http://idp/auth", user_id="user1",
        )
        # Force the token to be "expiring soon"
        manager._cache._cache[cache_key].expires_at = time.monotonic() + 100

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-access",
            "expires_in": 3600,
            "refresh_token": "new-refresh",
        }

        with patch("holmes.plugins.toolsets.mcp.oauth_token_manager.httpx.post", return_value=mock_response):
            with patch.object(manager._store, "store_token") as mock_store:
                manager._refresh_expiring_tokens()

        # Token should be refreshed in cache
        assert manager._cache.get_valid_access_token(cache_key) == "new-access"
        # Token should be pushed to persistent store
        mock_store.assert_called_once()
        manager.shutdown()

    def test_sweep_checks_store_when_no_refresh_token(self):
        """Tokens without refresh tokens check persistent store for a fresher version."""
        manager = self._make_manager()
        cache_key = "user2:__no_conv__:http://idp2/auth:cid2"

        manager._cache.set(
            cache_key, "old-access", expires_in=600,
            token_url="http://idp2/token", client_id="cid2",
            authorization_url="http://idp2/auth", user_id="user2",
        )
        manager._cache._cache[cache_key].expires_at = time.monotonic() + 100

        mock_store = MagicMock()
        mock_store.get_token.return_value = {"access_token": "store-access", "expires_in": 7200}
        manager._store = mock_store

        manager._refresh_expiring_tokens()

        assert manager._cache.get_valid_access_token(cache_key) == "store-access"
        mock_store.get_token.assert_called_once_with("http://idp2/auth", user_id="user2")
        manager.shutdown()

    def test_sweep_skips_non_expiring_tokens(self):
        """Tokens not expiring soon are left alone."""
        manager = self._make_manager()
        cache_key = "user3:__no_conv__:http://idp3/auth:cid3"

        manager._cache.set(
            cache_key, "fresh-access", expires_in=7200,
            refresh_token="refresh-tok",
            token_url="http://idp3/token", client_id="cid3",
            authorization_url="http://idp3/auth", user_id="user3",
        )

        with patch("holmes.plugins.toolsets.mcp.oauth_token_manager.httpx.post") as mock_post:
            manager._refresh_expiring_tokens()

        # No refresh should have been attempted
        mock_post.assert_not_called()
        assert manager._cache.get_valid_access_token(cache_key) == "fresh-access"
        manager.shutdown()

    def test_sweep_falls_back_to_store_when_refresh_fails(self):
        """When refresh fails, sweep checks persistent store for a fresher token."""
        manager = self._make_manager()
        cache_key = "user4:__no_conv__:http://idp4/auth:cid4"

        manager._cache.set(
            cache_key, "old-access", expires_in=600,
            refresh_token="bad-refresh",
            token_url="http://idp4/token", client_id="cid4",
            authorization_url="http://idp4/auth", user_id="user4",
        )
        manager._cache._cache[cache_key].expires_at = time.monotonic() + 100

        # Refresh fails
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_store = MagicMock()
        mock_store.get_token.return_value = {"access_token": "store-fallback", "expires_in": 3600}
        manager._store = mock_store

        with patch("holmes.plugins.toolsets.mcp.oauth_token_manager.httpx.post", return_value=mock_response):
            manager._refresh_expiring_tokens()

        assert manager._cache.get_valid_access_token(cache_key) == "store-fallback"
        manager.shutdown()

    def test_expired_refresh_token_still_tried_before_reauth(self):
        """An expired refresh token is still sent to the IdP — the IdP decides if it's valid."""
        manager = self._make_manager()

        oauth = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://idp6/auth",
            token_url="http://idp6/token",
            client_id="cid6",
        )
        request_context = {"user_id": "user6"}
        cache_key = manager.get_cache_key(oauth, request_context)

        # Store token with refresh, then expire BOTH access and refresh
        manager._cache.set(
            cache_key, "old-access", expires_in=60,
            refresh_token="expired-refresh",
            refresh_expires_in=3600,
            token_url="http://idp6/token", client_id="cid6",
            authorization_url="http://idp6/auth", user_id="user6",
        )
        manager._cache._cache[cache_key].expires_at = time.monotonic() - 1
        manager._cache._cache[cache_key].refresh_expires_at = time.monotonic() - 1

        # IdP accepts the expired refresh token and returns a new access token
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-from-expired-refresh",
            "expires_in": 3600,
            "refresh_token": "new-refresh",
        }

        with patch("holmes.plugins.toolsets.mcp.oauth_token_manager.httpx.post", return_value=mock_response) as mock_post:
            token = manager.get_access_token(oauth, request_context)

        # Should have tried the expired refresh token
        assert mock_post.called
        call_data = mock_post.call_args[1]["data"]
        assert call_data["grant_type"] == "refresh_token"
        assert call_data["refresh_token"] == "expired-refresh"

        # Should have gotten a new token
        assert token == "new-from-expired-refresh"
        manager.shutdown()

    def test_expired_refresh_token_rejected_returns_none(self):
        """If IdP rejects the expired refresh token, get_access_token returns None (user must reauth)."""
        manager = self._make_manager()

        oauth = MCPOAuthConfig(
            enabled=True,
            authorization_url="http://idp7/auth",
            token_url="http://idp7/token",
            client_id="cid7",
        )
        request_context = {"user_id": "user7"}
        cache_key = manager.get_cache_key(oauth, request_context)

        manager._cache.set(
            cache_key, "old-access", expires_in=60,
            refresh_token="dead-refresh",
            refresh_expires_in=3600,
            token_url="http://idp7/token", client_id="cid7",
            authorization_url="http://idp7/auth", user_id="user7",
        )
        manager._cache._cache[cache_key].expires_at = time.monotonic() - 1
        manager._cache._cache[cache_key].refresh_expires_at = time.monotonic() - 1

        # IdP rejects the expired refresh token
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("holmes.plugins.toolsets.mcp.oauth_token_manager.httpx.post", return_value=mock_response):
            token = manager.get_access_token(oauth, request_context)

        # No token — user must re-authenticate
        assert token is None
        manager.shutdown()

    def test_sweep_no_op_when_no_dal_and_no_refresh(self):
        """Without DAL and refresh token, sweep is a no-op."""
        manager = self._make_manager()
        cache_key = "user5:__no_conv__:http://idp5/auth:cid5"

        manager._cache.set(
            cache_key, "old-access", expires_in=600,
            token_url="http://idp5/token", client_id="cid5",
            authorization_url="http://idp5/auth", user_id="user5",
        )
        manager._cache._cache[cache_key].expires_at = time.monotonic() + 100

        # No DAL, no refresh token
        manager._refresh_expiring_tokens()

        # Token unchanged
        assert manager._cache.get_valid_access_token(cache_key) == "old-access"
        manager.shutdown()


# ---------------------------------------------------------------------------
