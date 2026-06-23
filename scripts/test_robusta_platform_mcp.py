"""Drive the robusta_platform_mcp toolset against a running platform-mcp.

This script bypasses the conversations worker (which would require a
live LLM + the K8s runner harness) and exercises the slimmest possible
path: construct the toolset with a real DAL, ask it to list tools, then
call post_slack_message via the underlying MCP client.

Prereqs:
  - ROBUSTA_UI_TOKEN, ROBUSTA_ACCOUNT_ID, CLUSTER_NAME must be set so
    SupabaseDal initialises successfully.
  - ROBUSTA_MCP_ENDPOINT must point at a reachable relay platform-mcp
    (default in this script is ``http://127.0.0.1:5101/api/platform-mcp``).

Usage:
  ROBUSTA_MCP_ENDPOINT=http://127.0.0.1:5101/api/platform-mcp \
      poetry run python scripts/test_robusta_platform_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict

from mcp.types import CallToolResult, ListToolsResult

from holmes.core.supabase_dal import SupabaseDal
from holmes.plugins.toolsets.mcp.toolset_mcp import (
    RemoteMCPToolset,
    get_initialized_mcp_session,
)
from holmes.plugins.toolsets.robusta_platform_mcp.robusta_platform_mcp import (
    make_robusta_platform_mcp_toolset,
)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("holmes_mcp_smoke")

os.environ.setdefault("ROBUSTA_MCP_ENDPOINT", "http://127.0.0.1:5101/api/platform-mcp")


async def _list_tools(toolset: RemoteMCPToolset) -> ListToolsResult:
    """Drive a tools/list against the configured MCP endpoint."""
    async with get_initialized_mcp_session(toolset) as session:
        return await session.list_tools()


async def _call_tool(
    toolset: RemoteMCPToolset, name: str, arguments: Dict[str, Any]
) -> CallToolResult:
    async with get_initialized_mcp_session(toolset) as session:
        return await session.call_tool(name, arguments)


def main() -> None:
    dal = SupabaseDal(cluster=os.environ.get("CLUSTER_NAME", "test"))
    if not dal.enabled:
        logger.error("DAL did not initialise — check ROBUSTA_UI_TOKEN")
        raise RuntimeError("DAL did not initialise")
    logger.info("DAL initialised for account=%s url=%s", dal.account_id, dal.url)

    toolset = make_robusta_platform_mcp_toolset(dal)
    if toolset is None:
        logger.error("toolset is None — DAL should be enabled here")
        raise RuntimeError("make_robusta_platform_mcp_toolset returned None")
    logger.info(
        "Toolset constructed: name=%s url=%s",
        toolset.name,
        toolset._mcp_config.url,
    )

    # Verify the dynamic auth header is built correctly. We never log the
    # bearer token contents — just confirm the prefix shape.
    headers = toolset._render_headers()
    if not headers or "Authorization" not in headers:
        logger.error("toolset did not produce an Authorization header")
        raise RuntimeError("missing Authorization header")
    if not headers["Authorization"].startswith(f"Bearer {dal.account_id} "):
        logger.error(
            "Authorization header did not start with the expected account prefix"
        )
        raise RuntimeError("malformed Authorization header")
    logger.info(
        "[ok] dynamic bearer header present for account=%s (token redacted)",
        dal.account_id,
    )

    # tools/list via the real MCP client.
    tools = asyncio.run(_list_tools(toolset))
    names = {t.name for t in tools.tools}
    logger.info("tools/list returned: %s", names)
    if "post_slack_message" not in names:
        logger.error("post_slack_message missing from tools/list: %s", names)
        raise RuntimeError("post_slack_message not advertised")

    # tools/call — the test account in this sandbox has no Slack
    # integration, so we expect a clean isError back. If a channel IS
    # configured (set MCP_SMOKE_HAS_SLACK=1) we accept ok=True instead.
    channel = os.environ.get("MCP_SMOKE_CHANNEL", "#test-holmes-mcp")
    result = asyncio.run(
        _call_tool(
            toolset,
            "post_slack_message",
            {"channel": channel, "markdown": "hi from holmes mcp smoke test"},
        )
    )
    payload = result.content[0].text if result.content else ""
    logger.info("tools/call returned isError=%s payload=%s", result.isError, payload)

    if os.environ.get("MCP_SMOKE_HAS_SLACK") == "1":
        if result.isError:
            logger.error("tools/call returned an error: %s", payload)
            raise RuntimeError("post_slack_message failed")
        body = json.loads(payload)
        if body.get("ok") is not True:
            logger.error("tools/call did not return ok=true: %s", body)
            raise RuntimeError("post_slack_message returned ok != True")
        logger.info("[ok] message posted: ts=%s", body.get("ts"))
    else:
        # Negative path: relay correctly says no integration configured.
        if not result.isError:
            logger.error("expected isError for account with no Slack: %s", payload)
            raise RuntimeError("expected isError but got success")
        if "no Slack integration" not in payload:
            logger.error("unexpected error payload: %s", payload)
            raise RuntimeError("unexpected error payload")
        logger.info("[ok] negative path: %s", payload)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("FAIL")
        sys.exit(2)
