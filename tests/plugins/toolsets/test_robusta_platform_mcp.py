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


ALWAYS_SENT = {"X-Robusta-Holmes-Version", "X-Robusta-User-Id"}


def _assert_no_authorization(headers):
    """The error-path contract: no Authorization key survives (any case),
    non-auth headers are kept, and the always-sent identity headers
    (version + user id) are present — the relay's executor version gate and
    RBAC depend on them being on every request."""
    assert headers is not None
    assert not any(k.lower() == "authorization" for k in headers)
    assert ALWAYS_SENT <= set(headers)


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
    _assert_no_authorization(headers)
    assert headers["X-Other"] == "keep"


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
    _assert_no_authorization(headers)
    assert headers["X-Other"] == "keep"


def test_render_headers_drops_auth_but_keeps_identity_headers_on_error():
    """Even when the ONLY header from the base impl was a stale Authorization,
    the result still carries the always-sent identity headers (version +
    user id) — and no Authorization."""
    dal = MagicMock()
    dal.enabled = True
    dal.get_ai_credentials.side_effect = RuntimeError("supabase down")
    toolset = make_robusta_platform_mcp_toolset(dal)
    assert toolset is not None

    with _patch_base_render_headers({"Authorization": "Bearer stale-token"}):
        headers = toolset._render_headers()
    _assert_no_authorization(headers)
    assert set(headers) == ALWAYS_SENT


def test_render_headers_injects_cluster_and_conversation_headers():
    """cluster_name and conversation_id from request_context are hardwired
    onto every MCP request as X-Robusta-* headers so the relay can pass
    them into the tool handler without trusting LLM-supplied arguments."""
    dal = MagicMock()
    dal.enabled = True
    dal.get_ai_credentials.return_value = ("acct-1", "tok-abc")
    toolset = make_robusta_platform_mcp_toolset(dal)
    assert toolset is not None

    headers = toolset._render_headers(
        {"cluster_name": "prod-eu", "conversation_id": "conv-42"}
    )
    assert headers is not None
    assert headers["X-Robusta-Cluster"] == "prod-eu"
    assert headers["X-Robusta-Conversation-Id"] == "conv-42"
    assert headers["Authorization"] == "Bearer acct-1 tok-abc"


def test_render_headers_omits_robusta_headers_when_context_missing():
    """When cluster_name / conversation_id aren't in the request context
    (e.g. CLI mode), don't emit empty X-Robusta-* headers."""
    dal = MagicMock()
    dal.enabled = True
    dal.get_ai_credentials.return_value = ("acct-1", "tok-abc")
    toolset = make_robusta_platform_mcp_toolset(dal)
    assert toolset is not None

    headers = toolset._render_headers(None)
    assert headers is not None
    assert "X-Robusta-Cluster" not in headers
    assert "X-Robusta-Conversation-Id" not in headers

    headers_empty_ctx = toolset._render_headers({})
    assert headers_empty_ctx is not None
    assert "X-Robusta-Cluster" not in headers_empty_ctx
    assert "X-Robusta-Conversation-Id" not in headers_empty_ctx


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
    _assert_no_authorization(headers)
    assert headers["X-Other"] == "keep"
