"""Drive the robusta_platform_mcp toolset against a running platform-mcp.

This script bypasses the conversations worker (which would require a
live LLM + the K8s runner harness) and exercises the slimmest possible
path: construct the toolset with a real DAL, ask it to list tools, then
call post_slack_message via the underlying MCP client.

Prereqs:
  - ROBUSTA_UI_TOKEN, ROBUSTA_ACCOUNT_ID, CLUSTER_NAME must be set so
    SupabaseDal initialises successfully.
  - ROBUSTA_MCP_ENDPOINT must point at a reachable relay platform-mcp
    (default in this script is ``http://127.0.0.1:5101/api/mcp``).

Usage:
  ROBUSTA_MCP_ENDPOINT=http://127.0.0.1:5101/api/mcp \
      poetry run python scripts/test_robusta_platform_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("holmes_mcp_smoke")

os.environ.setdefault("ROBUSTA_MCP_ENDPOINT", "http://127.0.0.1:5101/api/mcp")


async def _list_tools(toolset):
    """Drive a tools/list against the configured MCP endpoint."""
    from holmes.plugins.toolsets.mcp.toolset_mcp import get_initialized_mcp_session

    async with get_initialized_mcp_session(toolset) as session:
        return await session.list_tools()


async def _call_tool(toolset, name: str, arguments: dict):
    from holmes.plugins.toolsets.mcp.toolset_mcp import get_initialized_mcp_session

    async with get_initialized_mcp_session(toolset) as session:
        return await session.call_tool(name, arguments)


def main():
    from holmes.core.supabase_dal import SupabaseDal
    from holmes.plugins.toolsets.robusta_platform_mcp.robusta_platform_mcp import (
        make_robusta_platform_mcp_toolset,
    )

    dal = SupabaseDal(cluster=os.environ.get("CLUSTER_NAME", "test"))
    assert dal.enabled, "DAL did not initialise — check ROBUSTA_UI_TOKEN"
    logger.info("DAL initialised for account=%s url=%s", dal.account_id, dal.url)

    toolset = make_robusta_platform_mcp_toolset(dal)
    assert toolset is not None, "toolset is None — DAL should be enabled here"
    logger.info(
        "Toolset constructed: name=%s url=%s",
        toolset.name,
        toolset._mcp_config.url,
    )

    # Verify the dynamic auth header is built correctly.
    headers = toolset._render_headers()
    assert headers and headers["Authorization"].startswith(
        f"Bearer {dal.account_id} "
    ), headers
    logger.info("[ok] dynamic bearer header: %s...", headers["Authorization"][:60])

    # tools/list via the real MCP client.
    tools = asyncio.run(_list_tools(toolset))
    names = {t.name for t in tools.tools}
    logger.info("tools/list returned: %s", names)
    assert "post_slack_message" in names, names

    # tools/call — the test account in this sandbox has no Slack
    # integration, so we expect a clean isError back. If a channel IS
    # configured (set MCP_SMOKE_HAS_SLACK=1) we accept ok=True instead.
    channel = os.environ.get("MCP_SMOKE_CHANNEL", "#test-holmes-mcp")
    result = asyncio.run(
        _call_tool(
            toolset,
            "post_slack_message",
            {"channel": channel, "text": "hi from holmes mcp smoke test"},
        )
    )
    payload = result.content[0].text if result.content else ""
    logger.info("tools/call returned isError=%s payload=%s", result.isError, payload)

    if os.environ.get("MCP_SMOKE_HAS_SLACK") == "1":
        assert not result.isError, payload
        body = json.loads(payload)
        assert body.get("ok") is True, body
        logger.info("[ok] message posted: ts=%s", body.get("ts"))
    else:
        # Negative path: relay correctly says no integration configured.
        assert result.isError, "expected isError for account with no Slack"
        assert "no Slack integration" in payload, payload
        logger.info("[ok] negative path: %s", payload)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        logger.error("FAIL: %s", e)
        sys.exit(2)
