"""Robusta Platform MCP toolset (auto-enabled when DAL is available).

This toolset is a thin specialization of `RemoteMCPToolset` that wires
Holmes to the relay-hosted MCP endpoint (`platform-mcp`).

Why a subclass rather than a YAML config:
- The Authorization header is dynamic: it carries the *current* session
  token which is created/refreshed via `SupabaseDal.create_session_token()`.
  YAML headers (or `extra_headers`) are static and would pin a token that
  expires after 23h.
- The toolset only makes sense when DAL is enabled. We gate construction
  on `dal.enabled` at load time, and ship `enabled=True` by default so
  Robusta-managed Holmes installs activate it with zero config.

The user can opt out via the standard toolset disable mechanism:
``toolsets.robusta_platform_mcp.enabled: false`` in their Holmes config.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from pydantic import AnyUrl, PrivateAttr

from holmes.common.env_vars import ROBUSTA_API_ENDPOINT
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import (
    StructuredToolResult,
    ToolInvokeContext,
    ToolsetStatusEnum,
    ToolsetTag,
)
from holmes.plugins.toolsets.mcp.toolset_mcp import (
    MCPConfig,
    MCPMode,
    RemoteMCPTool,
    RemoteMCPToolset,
)
from holmes.version import get_version

logger = logging.getLogger(__name__)

TOOLSET_NAME = "robusta_platform_mcp"


def _without_authorization(headers: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Return a copy of ``headers`` with the ``Authorization`` key removed.

    Used on the error paths in ``_render_headers``: we must never let a
    stale Authorization header injected by the base implementation leak
    into a request when this toolset cannot mint a fresh session token.
    """
    if not headers:
        return None
    sanitized = {k: v for k, v in headers.items() if k.lower() != "authorization"}
    return sanitized or None


class RobustaPlatformMCPTool(RemoteMCPTool):
    """RemoteMCPTool that forwards per-call invocation context to platform-mcp.

    `MCPTool._invoke` only passes `context.request_context` down to header
    rendering — the rest of `ToolInvokeContext` (tool_call_id,
    max_token_count) never reaches `_render_headers`. Remote tool execution
    needs those per call: the single-tool token budget is a function of the
    CALLER's LLM and cannot be derived on the executor. So we enrich
    request_context before delegating; `_render_headers` maps the keys to
    X-Robusta-* headers.
    """

    def _invoke(
        self, params: dict, context: ToolInvokeContext
    ) -> StructuredToolResult:
        enriched = {
            **(context.request_context or {}),
            "tool_call_id": context.tool_call_id,
            "max_token_count": context.max_token_count,
        }
        return super()._invoke(
            params, context.model_copy(update={"request_context": enriched})
        )


class RobustaPlatformMCPToolset(RemoteMCPToolset):
    """RemoteMCPToolset wired to the relay `/api/platform-mcp` endpoint with
    dynamic session-token auth."""

    _dal: Optional[SupabaseDal] = PrivateAttr(default=None)

    def _load_remote_tools(self, request_context=None):
        # Same discovery as the base class, but construct our tool subclass
        # so every invocation carries the per-call context headers.
        if request_context:
            tools_result = asyncio.run(
                self._get_server_tools_with_context(request_context)
            )
        else:
            tools_result = asyncio.run(self._get_server_tools())
        return [
            RobustaPlatformMCPTool.create(tool, self) for tool in tools_result.tools
        ]

    def _render_headers(
        self, request_context: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, str]]:
        # Start from the base implementation so users can still inject extra
        # headers via config if they need to.
        headers: Dict[str, str] = super()._render_headers(request_context) or {}

        # Hardwire cluster_name and conversation_id into every request as
        # transport headers. The relay reads these off the request and
        # passes them into the tool handler's MCPCallContext — keeping
        # them out of the LLM-visible tool schema so the model can't
        # forge or omit them.
        if request_context:
            cluster_name = request_context.get("cluster_name")
            if cluster_name:
                headers["X-Robusta-Cluster"] = str(cluster_name)
            conversation_id = request_context.get("conversation_id")
            if conversation_id:
                headers["X-Robusta-Conversation-Id"] = str(conversation_id)
            tool_call_id = request_context.get("tool_call_id")
            if tool_call_id:
                headers["X-Robusta-Tool-Call-Id"] = str(tool_call_id)
            max_token_count = request_context.get("max_token_count")
            if max_token_count:
                headers["X-Robusta-Max-Tool-Tokens"] = str(max_token_count)

        # Always sent, independent of any feature flag: the executor version
        # gate and per-user RBAC on the relay depend on these.
        headers["X-Robusta-Holmes-Version"] = get_version()
        user_id = (request_context or {}).get("user_id")
        headers["X-Robusta-User-Id"] = str(user_id) if user_id else "None"

        dal = self._dal
        if dal is None or not dal.enabled:
            # Should not happen since we only construct the toolset when DAL
            # is enabled — but be defensive so we never serve up a request
            # with a stale Authorization header inherited from the base
            # implementation.
            return _without_authorization(headers)

        try:
            account_id, token = dal.get_ai_credentials()
        except Exception:
            logger.warning(
                "robusta_platform_mcp: failed to mint session token; "
                "request will likely be rejected",
                exc_info=True,
            )
            return _without_authorization(headers)

        headers["Authorization"] = f"Bearer {account_id} {token}"
        return headers


def refresh_platform_mcp_tools(tool_executor: Any) -> bool:
    """Re-discover platform-mcp tools and patch them into a live ToolExecutor.

    The dynamic remote-tool surface changes while Holmes runs (a new cluster
    publishes its tools; an account flag flips): without this, a caller only
    sees the tool list discovered at startup until the pod restarts. Called
    from the periodic toolset-refresh loop in server.py.

    Returns True when the tool list changed.
    """
    for toolset in getattr(tool_executor, "toolsets", []):
        if not isinstance(toolset, RobustaPlatformMCPToolset):
            continue
        if toolset.status != ToolsetStatusEnum.ENABLED:
            return False
        try:
            new_tools = toolset._load_remote_tools()
        except Exception:
            logger.warning(
                "robusta_platform_mcp: periodic tool re-discovery failed; "
                "keeping the previous tool list",
                exc_info=True,
            )
            return False
        def _signature(tools):
            # Names alone are not enough: when a cluster joins, the dynamic
            # remote_* tool keeps its NAME but its schema changes (the
            # agent_name enum grows). Compare full schemas.
            return {
                t.name: (
                    t.description,
                    {k: p.model_dump() for k, p in (t.parameters or {}).items()},
                )
                for t in tools
            }

        old_names = {t.name for t in toolset.tools}
        new_names = {t.name for t in new_tools}
        if _signature(toolset.tools) == _signature(new_tools):
            return False
        for name in old_names - new_names:
            # Only remove entries this toolset owns.
            if tool_executor._tool_to_toolset.get(name) is toolset:
                tool_executor.tools_by_name.pop(name, None)
                tool_executor._tool_to_toolset.pop(name, None)
        toolset.tools = new_tools
        for tool in new_tools:
            owner = tool_executor._tool_to_toolset.get(tool.name)
            if owner is not None and owner is not toolset:
                logger.warning(
                    "robusta_platform_mcp: not overriding tool '%s' owned by "
                    "toolset '%s'",
                    tool.name,
                    owner.name,
                )
                continue
            if tool.icon_url is None and toolset.icon_url is not None:
                tool.icon_url = toolset.icon_url
            tool_executor.tools_by_name[tool.name] = tool
            tool_executor._tool_to_toolset[tool.name] = toolset
        logger.info(
            "robusta_platform_mcp: tool list refreshed (added=%s removed=%s)",
            sorted(new_names - old_names),
            sorted(old_names - new_names),
        )
        return True
    return False


def make_robusta_platform_mcp_toolset(
    dal: Optional[SupabaseDal],
) -> Optional[RobustaPlatformMCPToolset]:
    """Construct the toolset when DAL is enabled; otherwise return ``None``
    so the caller can skip it entirely (matching the self-hosted case)."""
    if dal is None or not dal.enabled:
        return None

    # Allow operators to override the MCP endpoint independently of the LLM
    # endpoint in case a region is rolling out the new service incrementally.
    mcp_base = os.environ.get("ROBUSTA_MCP_ENDPOINT") or f"{ROBUSTA_API_ENDPOINT}/api/platform-mcp"

    config = MCPConfig(
        mode=MCPMode.STREAMABLE_HTTP,
        url=AnyUrl(mcp_base),
        verify_ssl=True,
    )

    # NOTE: prerequisites are set by RemoteMCPToolset.model_post_init —
    # it always overrides them with its own CallablePrerequisite, so
    # passing one here would be silently discarded.
    toolset = RobustaPlatformMCPToolset(
        name=TOOLSET_NAME,
        description=(
            "Robusta-hosted MCP server. Lets Holmes perform actions in the "
            "customer's external systems (Slack, etc.) via tools the relay "
            "owns the credentials for. Tools are discovered at runtime; "
            "credentials never leave the relay."
        ),
        docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/robusta-platform-mcp/",
        icon_url="https://cdn.prod.website-files.com/633e9bac8f71dfb7a8e4c9a6/646be7710db810b14133bdb5_logo.svg",
        enabled=True,
        tags=[ToolsetTag.CORE],
        tools=[],
        config={
            "mode": MCPMode.STREAMABLE_HTTP.value,
            "url": mcp_base,
        },
    )
    toolset._is_core = True  # never remotely exposable (circular dependency)
    toolset._dal = dal
    toolset._mcp_config = config
    return toolset
