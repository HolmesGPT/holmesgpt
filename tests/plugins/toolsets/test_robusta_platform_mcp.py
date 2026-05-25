"""Unit tests for the robusta_platform_mcp toolset.

These cover the guardrails called out in the design doc: the toolset
must be absent when DAL is disabled, and must inject a fresh
``Bearer {account_id} {session_token}`` header on every call (never a
stale one inherited from the base ``RemoteMCPToolset``).
"""

from unittest.mock import MagicMock, patch

from holmes.plugins.toolsets.robusta_platform_mcp.robusta_platform_mcp import (
    TOOLSET_NAME,
    make_robusta_platform_mcp_toolset,
)


def test_returns_none_when_dal_disabled():
    assert make_robusta_platform_mcp_toolset(None) is None

    dal = MagicMock()
    dal.enabled = False
    assert make_robusta_platform_mcp_toolset(dal) is None


def test_constructs_when_dal_enabled():
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct-1"
    dal.get_ai_credentials.return_value = ("acct-1", "tok-1")

    toolset = make_robusta_platform_mcp_toolset(dal)
    assert toolset is not None
    assert toolset.name == TOOLSET_NAME
    assert toolset.enabled is True


def test_renders_dynamic_bearer_header():
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct-1"
    dal.get_ai_credentials.return_value = ("acct-1", "tok-abc")

    toolset = make_robusta_platform_mcp_toolset(dal)
    assert toolset is not None
    headers = toolset._render_headers()
    assert headers is not None
    assert headers["Authorization"] == "Bearer acct-1 tok-abc"


def _patch_base_render_headers(returned_headers):
    """Patch RemoteMCPToolset._render_headers to return a fixed dict so we
    can assert what the subclass does on top of it."""
    base = "holmes.plugins.toolsets.mcp.toolset_mcp.RemoteMCPToolset._render_headers"
    return patch(base, return_value=returned_headers)


def test_render_headers_strips_stale_authorization_when_dal_disabled():
    """If DAL flips disabled at runtime, never serve up a stale Authorization
    header inherited from the base implementation."""
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct-1"
    dal.get_ai_credentials.return_value = ("acct-1", "tok-abc")
    toolset = make_robusta_platform_mcp_toolset(dal)
    assert toolset is not None

    # Now simulate DAL becoming unavailable + the base implementation
    # injecting a stale Authorization plus an unrelated header.
    dal.enabled = False
    stale = {"Authorization": "Bearer stale-token", "X-Other": "keep"}
    with _patch_base_render_headers(stale):
        headers = toolset._render_headers()
    assert headers == {"X-Other": "keep"}


def test_render_headers_strips_stale_authorization_on_credentials_error():
    """If get_ai_credentials() raises, never serve up a stale Authorization
    header inherited from the base implementation."""
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct-1"
    dal.get_ai_credentials.side_effect = RuntimeError("supabase down")
    toolset = make_robusta_platform_mcp_toolset(dal)
    assert toolset is not None

    stale = {"Authorization": "Bearer stale-token", "X-Other": "keep"}
    with _patch_base_render_headers(stale):
        headers = toolset._render_headers()
    assert headers == {"X-Other": "keep"}


def test_render_headers_returns_none_when_only_stale_auth_on_error():
    """If the only header from the base impl was Authorization and we drop
    it, return None so the MCP client doesn't get an empty-but-truthy dict."""
    dal = MagicMock()
    dal.enabled = True
    dal.get_ai_credentials.side_effect = RuntimeError("supabase down")
    toolset = make_robusta_platform_mcp_toolset(dal)
    assert toolset is not None

    with _patch_base_render_headers({"Authorization": "Bearer stale-token"}):
        headers = toolset._render_headers()
    assert headers is None


def test_render_headers_strips_authorization_case_insensitively():
    """HTTP headers are case-insensitive; a user-supplied lowercase
    'authorization' in extra_headers must not leak through the sanitiser."""
    dal = MagicMock()
    dal.enabled = True
    dal.get_ai_credentials.side_effect = RuntimeError("supabase down")
    toolset = make_robusta_platform_mcp_toolset(dal)
    assert toolset is not None

    stale = {
        "authorization": "Bearer stale-lower",
        "AUTHORIZATION": "Bearer stale-upper",
        "X-Other": "keep",
    }
    with _patch_base_render_headers(stale):
        headers = toolset._render_headers()
    assert headers == {"X-Other": "keep"}
