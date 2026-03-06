"""Tests for HTTP header propagation across toolset types.

Verifies that extra_headers configured in toolset config sections are rendered
with request_context and propagated to:
1. Shared header rendering utility
2. Toolset base class (render_extra_headers reads from config)
3. HTTP toolset (merged into outgoing requests)
4. YAML toolset (exposed as environment variables)
5. MCP toolset (merged with static headers)
6. ToolInvokeContext (pre-rendered headers)
"""

import os
from typing import Any, Dict, Optional, Tuple
from unittest.mock import Mock, patch

import pytest

from holmes.utils.pydantic_utils import ToolsetConfig

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    YAMLTool,
    YAMLToolset,
)
from requests.structures import CaseInsensitiveDict

from holmes.utils.header_rendering import render_template_headers


# ---------------------------------------------------------------------------
# Shared utility tests
# ---------------------------------------------------------------------------

class TestCaseInsensitiveDict:
    def test_case_insensitive_lookup(self):
        d = CaseInsensitiveDict({"X-Tenant-Id": "abc"})
        assert d["x-tenant-id"] == "abc"
        assert d["X-TENANT-ID"] == "abc"
        assert d["X-Tenant-Id"] == "abc"

    def test_missing_key_raises(self):
        d = CaseInsensitiveDict({"Foo": "bar"})
        with pytest.raises(KeyError):
            _ = d["Missing"]

    def test_contains_case_insensitive(self):
        d = CaseInsensitiveDict({"X-Tenant-Id": "abc"})
        assert "x-tenant-id" in d
        assert "X-TENANT-ID" in d
        assert "missing" not in d

    def test_get_case_insensitive(self):
        d = CaseInsensitiveDict({"X-Tenant-Id": "abc"})
        assert d.get("x-tenant-id") == "abc"
        assert d.get("X-TENANT-ID") == "abc"
        assert d.get("missing") is None
        assert d.get("missing", "default") == "default"


class TestRenderTemplateHeaders:
    def test_static_value(self):
        result = render_template_headers({"X-Static": "hello"})
        assert result == {"X-Static": "hello"}

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_HEADER_VAR", "from-env")
        result = render_template_headers(
            {"X-Env": "{{ env.TEST_HEADER_VAR }}"}
        )
        assert result == {"X-Env": "from-env"}

    def test_request_context_header(self):
        ctx = {"headers": {"X-Tenant": "t-123"}}
        result = render_template_headers(
            {"X-Forwarded-Tenant": "{{ request_context.headers['X-Tenant'] }}"},
            request_context=ctx,
        )
        assert result == {"X-Forwarded-Tenant": "t-123"}

    def test_case_insensitive_request_context(self):
        ctx = {"headers": {"X-Token": "secret"}}
        result = render_template_headers(
            {"Auth": "{{ request_context.headers['x-token'] }}"},
            request_context=ctx,
        )
        assert result == {"Auth": "secret"}

    def test_missing_header_renders_empty(self):
        ctx = {"headers": {}}
        result = render_template_headers(
            {"X-Missing": "{{ request_context.headers['X-Nope'] }}"},
            request_context=ctx,
        )
        # Jinja2 catches KeyError from CaseInsensitiveDict and renders as
        # empty string (Undefined).  The header is included but empty.
        assert result["X-Missing"] == ""

    def test_no_request_context(self):
        result = render_template_headers(
            {"X-Static": "val"},
            request_context=None,
        )
        assert result == {"X-Static": "val"}

    def test_mixed_templates(self, monkeypatch):
        monkeypatch.setenv("API_SECRET", "s3cr3t")
        ctx = {"headers": {"X-Org": "org-42"}}
        result = render_template_headers(
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
# YAML toolset injects extra_env_vars template into tools at init
# ---------------------------------------------------------------------------

class TestYAMLToolsetExtraEnvVars:
    def test_no_template_when_no_config(self):
        ts = YAMLToolset(
            name="test",
            description="test",
            tools=[YAMLTool(name="t", description="t", command="echo hi")],
        )
        assert ts.tools[0]._extra_env_vars_template is None

    def test_no_template_when_config_has_no_extra_env_vars(self):
        ts = YAMLToolset(
            name="test",
            description="test",
            tools=[YAMLTool(name="t", description="t", command="echo hi")],
            config={"some_other_key": "val"},
        )
        assert ts.tools[0]._extra_env_vars_template is None

    def test_template_set_from_extra_env_vars(self):
        ts = YAMLToolset(
            name="test",
            description="test",
            tools=[YAMLTool(name="t", description="t", command="echo hi")],
            config={"extra_env_vars": {"X-Custom": "static-value"}},
        )
        assert ts.tools[0]._extra_env_vars_template == {"X-Custom": "static-value"}
        assert ts.tools[0]._toolset_name == "test"

    def test_tool_renders_with_request_context(self):
        ts = YAMLToolset(
            name="test",
            description="test",
            tools=[YAMLTool(name="t", description="t", command='echo "$HOLMES_HEADER_X_TENANT"')],
            config={
                "extra_env_vars": {
                    "X-Tenant": "{{ request_context.headers['X-Tenant-Id'] }}"
                }
            },
        )
        ctx = ToolInvokeContext.model_construct(
            tool_number=1, user_approved=False, llm=Mock(),
            max_token_count=1000, tool_call_id="c1", tool_name="t",
            request_context={"headers": {"X-Tenant-Id": "tenant-abc"}},
        )
        result = ts.tools[0]._invoke({}, ctx)
        assert result.data == "tenant-abc"

    def test_tool_renders_with_env(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "tok-123")
        ts = YAMLToolset(
            name="test",
            description="test",
            tools=[YAMLTool(name="t", description="t", command='echo "$HOLMES_HEADER_AUTHORIZATION"')],
            config={
                "extra_env_vars": {"Authorization": "Bearer {{ env.MY_TOKEN }}"}
            },
        )
        ctx = ToolInvokeContext.model_construct(
            tool_number=1, user_approved=False, llm=Mock(),
            max_token_count=1000, tool_call_id="c1", tool_name="t",
        )
        result = ts.tools[0]._invoke({}, ctx)
        assert result.data == "Bearer tok-123"


# ---------------------------------------------------------------------------
# Base Toolset extra_headers (used by HTTP/Python toolsets, NOT YAML)
# ---------------------------------------------------------------------------

class _ConfigWithExtraHeaders(ToolsetConfig):
    extra_headers: Optional[Dict[str, str]] = None


class TestToolsetExtraHeaders:
    def test_render_extra_headers_from_pydantic_config(self):
        """Verify render_extra_headers works with a Pydantic model config (attribute access)."""
        config = _ConfigWithExtraHeaders(extra_headers={"X-From-Model": "pydantic-value"})
        from holmes.core.tools import ToolsetYamlFromConfig

        ts = ToolsetYamlFromConfig(
            name="test",
            config=config,
        )
        assert ts.render_extra_headers() == {"X-From-Model": "pydantic-value"}


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
# YAML tool environment variable propagation tests
# ---------------------------------------------------------------------------

class TestYAMLToolHeaderEnvVars:
    def test_build_header_env_vars_basic(self):
        env_vars = YAMLTool.build_header_env_vars(
            {"X-Custom-Token": "abc123", "Authorization": "Bearer xyz"}
        )
        assert env_vars == {
            "HOLMES_HEADER_X_CUSTOM_TOKEN": "abc123",
            "HOLMES_HEADER_AUTHORIZATION": "Bearer xyz",
        }

    def test_build_header_env_vars_empty(self):
        assert YAMLTool.build_header_env_vars({}) == {}

    def test_build_header_env_vars_special_chars(self):
        env_vars = YAMLTool.build_header_env_vars({"X.Dotted.Header": "val"})
        assert "HOLMES_HEADER_X_DOTTED_HEADER" in env_vars
        assert env_vars["HOLMES_HEADER_X_DOTTED_HEADER"] == "val"

    def test_yaml_tool_command_with_header_env_var(self):
        """Verify that extra_env_vars_template on tool produces env vars in bash commands."""
        tool = YAMLTool(
            name="test_echo",
            description="Echo a header value",
            command='echo "$HOLMES_HEADER_X_TOKEN"',
        )
        tool._extra_env_vars_template = {"X-Token": "my-secret-token"}
        tool._toolset_name = "test"
        context = ToolInvokeContext.model_construct(
            tool_number=1,
            user_approved=False,
            llm=Mock(),
            max_token_count=1000,
            tool_call_id="call-1",
            tool_name="test_echo",
        )
        result = tool._invoke({}, context)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "my-secret-token"

    def test_yaml_tool_script_with_header_env_var(self):
        """Verify that extra_env_vars_template on tool produces env vars in bash scripts."""
        tool = YAMLTool(
            name="test_script",
            description="Script using a header",
            script='#!/bin/bash\necho "$HOLMES_HEADER_AUTHORIZATION"',
        )
        tool._extra_env_vars_template = {"Authorization": "Bearer tok-456"}
        tool._toolset_name = "test"
        context = ToolInvokeContext.model_construct(
            tool_number=1,
            user_approved=False,
            llm=Mock(),
            max_token_count=1000,
            tool_call_id="call-1",
            tool_name="test_script",
        )
        result = tool._invoke({}, context)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "Bearer tok-456"

    def test_yaml_tool_no_extra_headers(self):
        """Verify YAML tools still work when no template is set."""
        tool = YAMLTool(
            name="test_echo",
            description="Simple echo",
            command="echo hello",
        )
        context = ToolInvokeContext.model_construct(
            tool_number=1,
            user_approved=False,
            llm=Mock(),
            max_token_count=1000,
            tool_call_id="call-1",
            tool_name="test_echo",
        )
        result = tool._invoke({}, context)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "hello"

    def test_yaml_toolset_injects_template_into_tools(self):
        """Verify YAMLToolset passes extra_env_vars template to its tools at init."""
        toolset = YAMLToolset(
            name="test_yaml",
            description="Test",
            docs_url="",
            icon_url="",
            tools=[
                YAMLTool(name="t1", description="t1", command="echo hi"),
            ],
            config={"extra_env_vars": {"X-Token": "static-val"}},
        )
        tool = toolset.tools[0]
        assert tool._extra_env_vars_template == {"X-Token": "static-val"}
        assert tool._toolset_name == "test_yaml"


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

    def test_render_extra_headers_falls_through_to_base(self):
        """MCP toolsets no longer override render_extra_headers — the base Toolset
        implementation renders extra_headers from config normally.  The actual
        merging with static 'headers' happens in _render_headers() at connection time."""
        from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
            config={
                "url": "http://localhost:1234",
                "extra_headers": {"X-Dynamic": "value"},
            },
        )
        # Base class renders extra_headers normally (no override blocking it)
        result = mcp_toolset.render_extra_headers({"headers": {"X-Foo": "bar"}})
        assert result == {"X-Dynamic": "value"}

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
