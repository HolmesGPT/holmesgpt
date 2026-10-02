"""Unit tests for the robusta_platform_mcp toolset.

These cover the guardrails called out in the design doc: the toolset
must be absent when DAL is disabled, and must inject a fresh
``Bearer {account_id} {session_token}`` header on every call (never a
stale one inherited from the base ``RemoteMCPToolset``).
"""

from unittest.mock import MagicMock, patch

from holmes.plugins.toolsets.robusta_platform_mcp.robusta_platform_mcp import (
    TOOLSET_NAME,
    RobustaPlatformMCPTool,
    investigating_model,
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


def _toolset():
    dal = MagicMock()
    dal.enabled = True
    dal.account_id = "acct-1"
    dal.get_ai_credentials.return_value = ("acct-1", "tok-abc")
    return make_robusta_platform_mcp_toolset(dal)


def test_user_id_header_prefers_conversation_owner():
    # OAuth opt-out drops request_context.user_id; the RBAC header must still
    # name the conversation owner, never the automated literal.
    headers = _toolset()._render_headers({"conversation_owner_id": "owner-1"})
    assert headers["X-Robusta-User-Id"] == "owner-1"

    headers = _toolset()._render_headers(
        {"conversation_owner_id": "owner-1", "user_id": "other"}
    )
    assert headers["X-Robusta-User-Id"] == "owner-1"


def test_user_id_header_falls_back_to_user_id_then_none():
    assert _toolset()._render_headers({"user_id": "u-1"})["X-Robusta-User-Id"] == "u-1"
    assert _toolset()._render_headers({})["X-Robusta-User-Id"] == "None"
    assert _toolset()._render_headers(None)["X-Robusta-User-Id"] == "None"


# ---- FRO-518: the model that investigated travels as X-Robusta-Model ----


def test_render_headers_injects_model_header():
    headers = _toolset()._render_headers({"model": "Robusta/Opus 4.6"})
    assert headers["X-Robusta-Model"] == "Robusta/Opus 4.6"


def test_render_headers_omits_model_header_when_unknown():
    for ctx in (None, {}, {"model": None}, {"model": ""}):
        headers = _toolset()._render_headers(ctx)
        assert "X-Robusta-Model" not in headers, ctx


def test_investigating_model_prefers_the_model_key():
    llm = MagicMock()
    llm.name = "Robusta/Opus 4.6"
    llm.model = "bedrock/us.anthropic.claude-opus-4-6"
    assert investigating_model(llm) == "Robusta/Opus 4.6"


def test_investigating_model_falls_back_to_the_litellm_model():
    llm = MagicMock()
    llm.name = None
    llm.model = "anthropic/claude-sonnet-4-5"
    assert investigating_model(llm) == "anthropic/claude-sonnet-4-5"

    llm.name = "   "
    assert investigating_model(llm) == "anthropic/claude-sonnet-4-5"

    llm.model = ""
    assert investigating_model(llm) is None

    assert investigating_model(object()) is None


def _llm(name, model):
    from holmes.core.llm import LLM

    llm = MagicMock(spec=LLM)
    llm.name = name
    llm.model = model
    return llm


def _invoke_context(llm, request_context=None):
    from holmes.core.tools import ToolInvokeContext

    return ToolInvokeContext(
        llm=llm,
        max_token_count=4096,
        tool_call_id="call-1",
        tool_name="update_ai_triage_metadata",
        request_context=request_context,
    )


def _captured_invoke_context(llm, request_context=None):
    """The request_context RobustaPlatformMCPTool hands to the base _invoke."""
    tool = RobustaPlatformMCPTool(
        name="update_ai_triage_metadata", description="", toolset=_toolset()
    )
    seen = {}

    def fake_invoke(self, params, context):
        seen["context"] = context
        return MagicMock()

    base = "holmes.plugins.toolsets.mcp.toolset_mcp.RemoteMCPTool._invoke"
    with patch(base, new=fake_invoke):
        tool._invoke({}, _invoke_context(llm, request_context))
    return seen["context"].request_context


def test_invoke_enriches_request_context_with_the_running_model():
    llm = _llm("Robusta/Opus 4.6", "bedrock/us.anthropic.claude-opus-4-6")
    ctx = _captured_invoke_context(llm, {"conversation_id": "conv-1"})
    assert ctx["model"] == "Robusta/Opus 4.6"
    assert ctx["conversation_id"] == "conv-1"
    assert ctx["tool_call_id"] == "call-1"
    assert ctx["max_token_count"] == 4096


def test_invoke_model_comes_from_the_llm_not_the_request_context():
    """A model named upstream (a chat request's `model`, a passthrough header)
    is what was ASKED for; the header must name what actually runs."""
    llm = _llm("Robusta/Haiku 4.5", "anthropic/claude-haiku-4-5")
    ctx = _captured_invoke_context(llm, {"model": "Robusta/Opus 4.6"})
    assert ctx["model"] == "Robusta/Haiku 4.5"


def test_invoke_enriches_request_context_even_without_one():
    llm = _llm("gpt-4o", "openai/gpt-4o")
    ctx = _captured_invoke_context(llm, None)
    assert ctx["model"] == "gpt-4o"


def test_end_to_end_headers_carry_the_llm_model():
    """_invoke -> _render_headers: the header on the wire is the LLM's key."""
    llm = _llm("Robusta/Opus 4.6", "bedrock/us.anthropic.claude-opus-4-6")
    toolset = _toolset()
    ctx = _captured_invoke_context(llm, {"conversation_id": "conv-1"})
    headers = toolset._render_headers(ctx)
    assert headers["X-Robusta-Model"] == "Robusta/Opus 4.6"
    assert headers["X-Robusta-Conversation-Id"] == "conv-1"
    assert headers["X-Robusta-Tool-Call-Id"] == "call-1"
