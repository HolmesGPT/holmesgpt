"""Unit tests for the robusta_platform_mcp toolset.

These cover the guardrails called out in the design doc: the toolset
must be absent when DAL is disabled, must inject a fresh
``Bearer {account_id} {session_token}`` header on every call, and must
expose a hook to invalidate the cached token so the 401-retry path
mints a new one.
"""

from unittest.mock import MagicMock

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


def test_invalidate_session_token_clears_cache():
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct-1"
    # token_cache is a real dict-like; the toolset just calls .pop on it.
    dal.token_cache = {"session_token": "stale"}
    dal.get_ai_credentials.return_value = ("acct-1", "fresh")

    toolset = make_robusta_platform_mcp_toolset(dal)
    assert toolset is not None
    toolset.invalidate_session_token()
    assert "session_token" not in dal.token_cache
