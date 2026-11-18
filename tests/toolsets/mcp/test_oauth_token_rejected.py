"""An OAuth token the backend rejects during a tool call must lead back to the login prompt.

Passthrough MCP servers (the Kubernetes MCP server on OpenShift OAuth) never
answer 401 themselves: the API server's rejection comes back as ordinary tool
output, and with no refresh token the only recovery is a new sign-in.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import CallToolResult, TextContent

from holmes.core.oauth_config import MCPOAuthConfig
from holmes.core.oauth_utils import _get_token_manager
from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.mcp.oauth_token_store import DalTokenStore
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
        return_value="rejected-test-signing-key",
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


class TestTokenRejectedDuringToolCall:
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
