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

import logging
import os
from typing import Any, Dict, Optional

from pydantic import AnyUrl, PrivateAttr

from holmes.common.env_vars import ROBUSTA_API_ENDPOINT
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import StaticPrerequisite, ToolsetTag
from holmes.plugins.toolsets.mcp.toolset_mcp import (
    MCPConfig,
    MCPMode,
    RemoteMCPToolset,
)

logger = logging.getLogger(__name__)

TOOLSET_NAME = "robusta_platform_mcp"


class RobustaPlatformMCPToolset(RemoteMCPToolset):
    """RemoteMCPToolset wired to the relay `/api/mcp` endpoint with
    dynamic session-token auth."""

    _dal: Optional[SupabaseDal] = PrivateAttr(default=None)

    def _render_headers(
        self, request_context: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, str]]:
        # Start from the base implementation so users can still inject extra
        # headers via config if they need to.
        headers: Dict[str, str] = super()._render_headers(request_context) or {}

        dal = self._dal
        if dal is None or not dal.enabled:
            # Should not happen since we only construct the toolset when DAL
            # is enabled — but be defensive so we never serve up a request
            # with a missing or stale Authorization header.
            return headers or None

        try:
            account_id, token = dal.get_ai_credentials()
        except Exception:
            logger.exception(
                "robusta_platform_mcp: failed to mint session token; "
                "request will likely be rejected"
            )
            return headers or None

        headers["Authorization"] = f"Bearer {account_id} {token}"
        return headers

    def invalidate_session_token(self) -> None:
        """Drop the cached session token so the next request mints a fresh one.

        Called from the 401-retry path. The relay-side `validate_auth_token`
        rejects tokens that are missing, revoked, or older than
        ``HOLMES_TOKEN_EXPIRATION_SECONDS``; in any of those cases we want
        the next attempt to create a brand new ``AuthTokens`` row.
        """
        if self._dal is None:
            return
        try:
            self._dal.token_cache.pop("session_token", None)
        except Exception:
            logger.exception("robusta_platform_mcp: failed to clear session token cache")


def make_robusta_platform_mcp_toolset(
    dal: Optional[SupabaseDal],
) -> Optional[RobustaPlatformMCPToolset]:
    """Construct the toolset when DAL is enabled; otherwise return ``None``
    so the caller can skip it entirely (matching the self-hosted case)."""
    if dal is None or not dal.enabled:
        return None

    # Allow operators to override the MCP endpoint independently of the LLM
    # endpoint in case a region is rolling out the new service incrementally.
    mcp_base = os.environ.get("ROBUSTA_MCP_ENDPOINT") or f"{ROBUSTA_API_ENDPOINT}/api/mcp"

    config = MCPConfig(
        mode=MCPMode.STREAMABLE_HTTP,
        url=AnyUrl(mcp_base),
        verify_ssl=True,
    )

    enabled_prereq = StaticPrerequisite(
        enabled=True,
        disabled_reason="Robusta platform MCP requires DAL",
    )

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
        prerequisites=[enabled_prereq],
        tools=[],
        config={
            "mode": MCPMode.STREAMABLE_HTTP.value,
            "url": mcp_base,
        },
    )
    toolset._dal = dal
    toolset._mcp_config = config
    return toolset
