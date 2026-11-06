"""Tests for HTTP header propagation across toolset types.

Verifies that extra_headers configured in toolset config sections are rendered
with request_context and propagated to:
1. Shared header rendering utility
2. HTTP toolset (merged into outgoing requests)
3. YAML toolset (request_context available in Jinja2 command templates)
4. MCP toolset (merged with static headers)
5. ToolInvokeContext (pre-rendered headers)
"""

import os
from typing import Any, Dict, Optional, Tuple
from unittest.mock import Mock, patch

import pytest

from holmes.core.tools import (
    StructuredToolResultStatus,
    ToolInvokeContext,
    YAMLTool,
)
from holmes.utils.header_rendering import render_header_templates


# ---------------------------------------------------------------------------
# Shared utility tests
# ---------------------------------------------------------------------------

class TestRenderTemplateHeaders:
    def test_static_value(self):
        result = render_header_templates({"X-Static": "hello"})
        assert result == {"X-Static": "hello"}

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_HEADER_VAR", "from-env")
        result = render_header_templates(
            {"X-Env": "{{ env.TEST_HEADER_VAR }}"}
        )
        assert result == {"X-Env": "from-env"}

    def test_request_context_header(self):
        ctx = {"headers": {"X-Tenant": "t-123"}}
        result = render_header_templates(
            {"X-Forwarded-Tenant": "{{ request_context.headers['X-Tenant'] }}"},
            request_context=ctx,
        )
        assert result == {"X-Forwarded-Tenant": "t-123"}

    def test_case_insensitive_request_context(self):
        ctx = {"headers": {"X-Token": "secret"}}
        result = render_header_templates(
            {"Auth": "{{ request_context.headers['x-token'] }}"},
            request_context=ctx,
        )
        assert result == {"Auth": "secret"}

    def test_missing_header_renders_empty(self):
        ctx = {"headers": {}}
        result = render_header_templates(
            {"X-Missing": "{{ request_context.headers['X-Nope'] }}"},
            request_context=ctx,
        )
        # Jinja2 catches KeyError from CaseInsensitiveDict and renders as
        # empty string (Undefined).  The header is included but empty.
        assert result["X-Missing"] == ""

    def test_no_request_context(self):
        result = render_header_templates(
            {"X-Static": "val"},
            request_context=None,
        )
        assert result == {"X-Static": "val"}

    def test_mixed_templates(self, monkeypatch):
        monkeypatch.setenv("API_SECRET", "s3cr3t")
        ctx = {"headers": {"X-Org": "org-42"}}
        result = render_header_templates(
            {
                "Authorization": "Bearer {{ env.API_SECRET }}",
                "X-Org-Id": "{{ request_context.headers['X-Org'] }}",
                "X-Version": "v1",
            },
            request_context=ctx,
        )
        assert result["Authorization"] == "Bearer s3cr3t"
        assert result["X-Org-Id"] == "org-42"
        assert result["X-Version"] == "v1"


# ---------------------------------------------------------------------------
# YAML tool Jinja2 template context (request_context + env)
# ---------------------------------------------------------------------------

class TestYAMLToolTemplateContext:
    def test_command_renders_request_context_header(self):
        """request_context.headers is available in command Jinja2 templates."""
        tool = YAMLTool(
            name="t",
            description="t",
            command="echo {{ request_context.headers['X-Tenant-Id'] }}",
        )
        ctx = ToolInvokeContext.model_construct(
            tool_number=1, user_approved=False, llm=Mock(),
            max_token_count=1000, tool_call_id="c1", tool_name="t",
            request_context={"headers": {"X-Tenant-Id": "tenant-abc"}},
        )
        result = tool._invoke({}, ctx)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "tenant-abc"

    def test_command_renders_env_var(self, monkeypatch):
        """env vars are available in command Jinja2 templates."""
        monkeypatch.setenv("MY_TOKEN", "tok-123")
        tool = YAMLTool(
            name="t",
            description="t",
            command="echo Bearer {{ env.MY_TOKEN }}",
        )
        ctx = ToolInvokeContext.model_construct(
            tool_number=1, user_approved=False, llm=Mock(),
            max_token_count=1000, tool_call_id="c1", tool_name="t",
        )
        result = tool._invoke({}, ctx)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "Bearer tok-123"

    def test_command_works_without_request_context(self):
        """Commands work fine when no request_context is provided."""
        tool = YAMLTool(
            name="t",
            description="t",
            command="echo hello",
        )
        ctx = ToolInvokeContext.model_construct(
            tool_number=1, user_approved=False, llm=Mock(),
            max_token_count=1000, tool_call_id="c1", tool_name="t",
        )
        result = tool._invoke({}, ctx)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "hello"

    def test_script_renders_request_context_header(self):
        """request_context.headers is available in script Jinja2 templates."""
        tool = YAMLTool(
            name="t",
            description="t",
            script="#!/bin/bash\necho {{ request_context.headers['X-Auth'] }}",
        )
        ctx = ToolInvokeContext.model_construct(
            tool_number=1, user_approved=False, llm=Mock(),
            max_token_count=1000, tool_call_id="c1", tool_name="t",
            request_context={"headers": {"X-Auth": "Bearer secret"}},
        )
        result = tool._invoke({}, ctx)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "Bearer secret"

    def test_command_renders_case_insensitive_header(self):
        """Header lookups in YAML templates are case-insensitive."""
        tool = YAMLTool(
            name="t",
            description="t",
            command="echo {{ request_context.headers['x-tenant-id'] }}",
        )
        ctx = ToolInvokeContext.model_construct(
            tool_number=1, user_approved=False, llm=Mock(),
            max_token_count=1000, tool_call_id="c1", tool_name="t",
            request_context={"headers": {"X-Tenant-Id": "tenant-abc"}},
        )
        result = tool._invoke({}, ctx)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "tenant-abc"


# ---------------------------------------------------------------------------
# ToolInvokeContext tests
# ---------------------------------------------------------------------------

class TestToolInvokeContextHeaders:
    def test_model_dump_redacts_request_context_headers(self):
        ctx = ToolInvokeContext.model_construct(
            tool_number=1,
            user_approved=False,
            llm=Mock(),
            max_token_count=1000,
            tool_call_id="call-1",
            tool_name="test_tool",
            request_context={"headers": {"H1": "v1"}},
        )
        dumped = ctx.model_dump()
        # Entire request_context is aggressively redacted (keys only, values hidden)
        assert dumped["request_context"] == {"headers": "***REDACTED***"}




# ---------------------------------------------------------------------------
# HTTP toolset header propagation tests
# ---------------------------------------------------------------------------

class TestHttpToolsetHeaderPropagation:
    @patch("holmes.plugins.toolsets.http.http_toolset.requests.request")
    def test_extra_headers_merged_into_request(self, mock_request):
        """Verify that config-level extra_headers are merged into HTTP requests."""
        from holmes.plugins.toolsets.http.http_toolset import HttpRequest, HttpToolset

        # Create an HTTP toolset with extra_headers in config
        toolset = HttpToolset(
            name="test_http",
            enabled=True,
            config={
                "endpoints": [
                    {"hosts": ["api.example.com"], "methods": ["GET"]}
                ],
                "extra_headers": {"X-Custom": "static-val"},
            },
        )
        ok, _ = toolset.prerequisites_callable({
            "endpoints": [
                {"hosts": ["api.example.com"], "methods": ["GET"]}
            ],
            "extra_headers": {"X-Custom": "static-val"},
        })
        assert ok

        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test"}
        mock_request.return_value = mock_response

        tool = toolset.tools[0]
        ctx = Mock(spec=ToolInvokeContext)
        ctx.request_context = None

        result = tool._invoke(
            {"url": "https://api.example.com/test"},
            ctx,
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        # Verify the custom header was included in the request
        call_kwargs = mock_request.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["X-Custom"] == "static-val"

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.request")
    def test_extra_headers_override_defaults(self, mock_request):
        """Verify that extra_headers override default headers."""
        from holmes.plugins.toolsets.http.http_toolset import HttpRequest, HttpToolset

        toolset = HttpToolset(
            name="test_http",
            enabled=True,
            config={
                "endpoints": [
                    {"hosts": ["api.example.com"], "methods": ["GET"]}
                ],
                "default_headers": {"X-Default": "original"},
                "extra_headers": {"X-Default": "overridden"},
            },
        )
        ok, _ = toolset.prerequisites_callable({
            "endpoints": [
                {"hosts": ["api.example.com"], "methods": ["GET"]}
            ],
            "default_headers": {"X-Default": "original"},
            "extra_headers": {"X-Default": "overridden"},
        })
        assert ok

        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_request.return_value = mock_response

        tool = toolset.tools[0]
        ctx = Mock(spec=ToolInvokeContext)
        ctx.request_context = None

        result = tool._invoke(
            {"url": "https://api.example.com/test"},
            ctx,
        )

        call_kwargs = mock_request.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["X-Default"] == "overridden"


# ---------------------------------------------------------------------------
# MCP config-level extra_headers tests
# ---------------------------------------------------------------------------

class TestMCPConfigExtraHeaders:
    def test_config_level_extra_headers_rendered(self):
        """Verify that config-level extra_headers are rendered in MCP headers."""
        from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
            config={
                "url": "http://localhost:1234",
                "extra_headers": {"X-Config-Level": "from-config"},
            },
        )
        mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        rendered = mcp_toolset._render_headers(None)

        assert rendered is not None
        assert rendered["X-Config-Level"] == "from-config"

    def test_extra_headers_with_request_context(self):
        """Config-level extra_headers should render with request_context."""
        from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
            config={
                "url": "http://localhost:1234",
                "extra_headers": {
                    "X-Tenant": "{{ request_context.headers['X-Tenant-Id'] }}"
                },
            },
        )
        mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        ctx = {"headers": {"X-Tenant-Id": "tenant-from-request"}}
        rendered = mcp_toolset._render_headers(ctx)

        assert rendered is not None
        assert rendered["X-Tenant"] == "tenant-from-request"

    def test_static_headers_and_extra_headers_merged(self):
        """Static 'headers' and template 'extra_headers' should be merged."""
        from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
            config={
                "url": "http://localhost:1234",
                "headers": {"X-Static": "static-value"},
                "extra_headers": {"X-Dynamic": "dynamic-value"},
            },
        )
        mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        rendered = mcp_toolset._render_headers(None)

        assert rendered is not None
        assert rendered["X-Static"] == "static-value"
        assert rendered["X-Dynamic"] == "dynamic-value"

    def test_extra_headers_override_static_headers(self):
        """extra_headers should take precedence over static headers."""
        from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
            config={
                "url": "http://localhost:1234",
                "headers": {"X-Shared": "from-static"},
                "extra_headers": {"X-Shared": "from-extra"},
            },
        )
        mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        rendered = mcp_toolset._render_headers(None)

        assert rendered is not None
        assert rendered["X-Shared"] == "from-extra"


# ---------------------------------------------------------------------------
# ROB-1104: propagated headers must not bypass shell sanitization
# ---------------------------------------------------------------------------

class TestRequestContextShellInjection:
    """request_context reaches the YAMLTool bash sink; its values must be
    shell-quoted like tool params so metacharacters are inert (ROB-1104)."""

    def _ctx(self, request_context):
        return ToolInvokeContext.model_construct(
            tool_number=1, user_approved=False, llm=Mock(),
            max_token_count=1000, tool_call_id="c1", tool_name="t",
            request_context=request_context,
        )

    @pytest.mark.parametrize(
        "payload",
        ["$(id)", "`id`", "; id", "$(touch /tmp/rob1104_pwned)", "&& id"],
    )
    def test_command_header_metacharacters_are_rejected(self, payload):
        """A header with shell metacharacters is refused, never executed."""
        tool = YAMLTool(
            name="t", description="t",
            command="echo {{ request_context.headers['X-Tenant-Id'] }}",
        )
        result = tool._invoke(
            {}, self._ctx({"headers": {"X-Tenant-Id": payload}})
        )
        # The command is refused before it ever reaches bash, so `id` never runs.
        assert result.status == StructuredToolResultStatus.ERROR
        assert "metacharacter" in (result.error or "")
        # the whole disallowed set is listed, not just the character found
        assert "Disallowed:" in (result.error or "")
        assert "uid=" not in (result.error or "")
        assert "uid=" not in (result.data or "")

    @pytest.mark.parametrize("payload", ["$(id)", "`id`", "; id", "&& id"])
    def test_script_header_metacharacters_are_rejected(self, payload):
        """Same protection on the separate temporary-script sink."""
        tool = YAMLTool(
            name="t", description="t",
            script="#!/bin/bash\necho {{ request_context.headers['X-Auth'] }}",
        )
        result = tool._invoke(
            {}, self._ctx({"headers": {"X-Auth": payload}})
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "metacharacter" in (result.error or "")
        assert "uid=" not in (result.error or "")
        assert "uid=" not in (result.data or "")

    def test_documented_curl_auth_token_example_is_safe(self):
        """The documented `-H "X-Auth-Token: ..."` pattern nests the value in
        double quotes, where shlex.quote is not enough; the metacharacter reject
        makes a command-substitution payload inert regardless of quoting."""
        tool = YAMLTool(
            name="t", description="t",
            # Mirrors docs/data-sources/header-propagation.md, but echo instead
            # of curl so the test needs no network.
            command=(
                'echo "X-Auth-Token: '
                "{{ request_context.headers['X-Auth-Token'] }}\""
            ),
        )
        result = tool._invoke(
            {}, self._ctx({"headers": {"X-Auth-Token": "$(id)"}})
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "uid=" not in (result.error or "")
        assert "uid=" not in (result.data or "")

    def test_top_level_request_context_value_is_rejected(self):
        """Non-header request_context scalars (e.g. user_id) are checked too."""
        tool = YAMLTool(
            name="t", description="t",
            command="echo {{ request_context.user_id }}",
        )
        result = tool._invoke(
            {}, self._ctx({"headers": {}, "user_id": "$(id)"})
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "uid=" not in (result.error or "")
        assert "uid=" not in (result.data or "")

    def test_build_context_raises_on_metacharacter_header(self):
        """_build_context refuses a metacharacter header at the source."""
        from holmes.core.tools import ShellInjectionError

        tool = YAMLTool(
            name="t", description="t",
            command="echo {{ request_context.headers['X-Val'] }}",
        )
        with pytest.raises(ShellInjectionError):
            tool._build_context(
                {}, request_context={"headers": {"X-Val": "; rm -rf /"}}
            )

    def test_benign_header_with_whitespace_is_allowed(self):
        """A legitimate multi-word token (e.g. `Bearer <jwt>`) is permitted."""
        tool = YAMLTool(
            name="t", description="t",
            command="echo {{ request_context.headers['X-Auth'] }}",
        )
        result = tool._invoke(
            {}, self._ctx({"headers": {"X-Auth": "Bearer abc.def-ghi_jkl"}})
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "Bearer abc.def-ghi_jkl"

    def test_benign_header_still_renders_unchanged(self):
        """A normal token passes through untouched (no regression)."""
        tool = YAMLTool(
            name="t", description="t",
            command="echo {{ request_context.headers['X-Tenant-Id'] }}",
        )
        result = tool._invoke(
            {}, self._ctx({"headers": {"X-Tenant-Id": "tenant-abc"}})
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "tenant-abc"


# ---------------------------------------------------------------------------
# ROB-1104: common, legitimate header-propagation use cases keep working.
# These guard against the fix over-rejecting and breaking real users.
# ---------------------------------------------------------------------------

class TestLegitimateHeaderUseCases:
    def _ctx(self, request_context):
        return ToolInvokeContext.model_construct(
            tool_number=1, user_approved=False, llm=Mock(),
            max_token_count=1000, tool_call_id="c1", tool_name="t",
            request_context=request_context,
        )

    def test_documented_curl_pattern_with_real_token(self):
        """The docs `-H "X-Auth-Token: {{...}}"` pattern renders a JWT verbatim
        (no injected quotes) for a legitimate token."""
        tool = YAMLTool(
            name="t", description="t",
            command=(
                'echo "X-Auth-Token: '
                "{{ request_context.headers['X-Auth-Token'] }}\""
            ),
        )
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc-DEF_123"
        result = tool._invoke({}, self._ctx({"headers": {"X-Auth-Token": token}}))
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == f"X-Auth-Token: {token}"

    def test_documented_pattern_with_bearer_prefixed_token(self):
        """A `Bearer <token>` value (has a space) inside the double-quoted docs
        pattern is preserved exactly — no literal-quote corruption."""
        tool = YAMLTool(
            name="t", description="t",
            command=(
                'echo "Authorization: '
                "{{ request_context.headers['X-Downstream-Auth'] }}\""
            ),
        )
        value = "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
        result = tool._invoke(
            {}, self._ctx({"headers": {"X-Downstream-Auth": value}})
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == f"Authorization: {value}"

    def test_tenant_id_header_bare_word(self):
        tool = YAMLTool(
            name="t", description="t",
            command="echo tenant={{ request_context.headers['X-Tenant-Id'] }}",
        )
        result = tool._invoke(
            {}, self._ctx({"headers": {"X-Tenant-Id": "acme-prod-42"}})
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "tenant=acme-prod-42"

    def test_base64_api_key_preserved(self):
        """Base64 padding/slash/plus characters are legal and pass through."""
        tool = YAMLTool(
            name="t", description="t",
            command="echo {{ request_context.headers['X-Api-Key'] }}",
        )
        key = "YWxhZGRpbjpvcGVuc2VzYW1l+/=="
        result = tool._invoke({}, self._ctx({"headers": {"X-Api-Key": key}}))
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == key

    def test_multiple_headers_in_one_command(self):
        tool = YAMLTool(
            name="t", description="t",
            command=(
                "echo {{ request_context.headers['X-Tenant-Id'] }}"
                " {{ request_context.headers['X-Request-Id'] }}"
            ),
        )
        result = tool._invoke({}, self._ctx({"headers": {
            "X-Tenant-Id": "acme", "X-Request-Id": "req-9f3c",
        }}))
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "acme req-9f3c"

    def test_env_var_and_header_together(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "sekret-key")
        tool = YAMLTool(
            name="t", description="t",
            command=(
                "echo {{ env.INTERNAL_API_KEY }}"
                " {{ request_context.headers['X-Correlation-Id'] }}"
            ),
        )
        result = tool._invoke(
            {}, self._ctx({"headers": {"X-Correlation-Id": "corr-1"}})
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "sekret-key corr-1"

    def test_top_level_user_id_renders(self):
        tool = YAMLTool(
            name="t", description="t",
            command="echo user={{ request_context.user_id }}",
        )
        result = tool._invoke(
            {}, self._ctx({"headers": {}, "user_id": "u-12345"})
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "user=u-12345"

    def test_missing_header_renders_empty_and_succeeds(self):
        """Referencing an absent header renders empty (no crash) — unchanged."""
        tool = YAMLTool(
            name="t", description="t",
            command="echo start{{ request_context.headers['X-Absent'] }}end",
        )
        result = tool._invoke({}, self._ctx({"headers": {}}))
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "startend"

    def test_script_path_with_real_token(self):
        tool = YAMLTool(
            name="t", description="t",
            script="#!/bin/bash\necho token={{ request_context.headers['X-Token'] }}",
        )
        result = tool._invoke(
            {}, self._ctx({"headers": {"X-Token": "abc123DEF"}})
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "token=abc123DEF"
