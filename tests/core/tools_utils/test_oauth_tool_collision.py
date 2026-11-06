"""A per-user OAuth tool must not collide with an already-exposed tool name.

Holmes resolves MCP name collisions while building the base tool list, but an
OAuth toolset contributes only a ``_connect`` placeholder at that point: its real
tools are appended later, per user, by ``OAuthToolConnector.apply_user_tools``.
A name that is unique at construction time can therefore collide once the user's
tools appear.

The failure this guards against is not cosmetic: two tools with the same name
reach the model and the provider rejects the entire request with
"Tool names must be unique", before any tool runs. The fix exposes the colliding
user tool under ``<toolset>__<tool>`` and records that name so invocation and
toolset lookup still resolve.

Reported scenario: Coroot's MCP server and the n9e MCP server both expose
``query_logs``; the Coroot tool arrives over OAuth.
"""

from typing import List

import pytest

from holmes.core.tools import Tool, Toolset, ToolsetStatusEnum
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig, RemoteMCPTool, RemoteMCPToolset
from tests.mocks.toolset_mocks import DummyTool


class _BaseToolset(Toolset):
    """A non-OAuth toolset holding one plain tool, to own a colliding name."""

    name: str = "nightingale"
    description: str = "base toolset"
    enabled: bool = True

    def __init__(self, tool_names: List[str], **kwargs):
        super().__init__(**kwargs)
        self.status = ToolsetStatusEnum.ENABLED
        self.tools = [DummyTool(name=n) for n in tool_names]


def _mcp_toolset(name: str, tool_names: List[str]) -> RemoteMCPToolset:
    """A RemoteMCPToolset exposing `tool_names` as already-loaded MCP tools."""
    ts = RemoteMCPToolset(name=name, enabled=True)
    ts.status = ToolsetStatusEnum.ENABLED
    ts._mcp_config = MCPConfig(url="http://mcp:8000", mode="streamable-http")
    ts.tools = [
        RemoteMCPTool(name=n, description="", parameters={}, toolset=ts, mcp_tool_name=n)
        for n in tool_names
    ]
    return ts


def _oauth_tool(name: str, toolset: RemoteMCPToolset) -> RemoteMCPTool:
    """A tool as `load_tools_for_user` would produce it from the MCP server."""
    return RemoteMCPTool(name=name, description="", parameters={}, toolset=toolset, mcp_tool_name=name)


@pytest.fixture
def executor_with_collision():
    """Base list already exposes `query_logs`; an OAuth toolset adds its own."""
    base = _BaseToolset(["query_logs"])
    mcp = _mcp_toolset("coroot", ["list_applications"])
    executor = ToolExecutor([base, mcp])
    # The MCP toolset is authenticated for this user: its real tools replace the
    # placeholder in the per-user store.
    executor.oauth_connector.store_user_tools(
        "user-1", "coroot", [_oauth_tool("query_logs", mcp), _oauth_tool("list_applications", mcp)]
    )
    return executor, base, mcp


def test_colliding_oauth_tool_is_namespaced(executor_with_collision):
    """The final tool list must contain no duplicate names."""
    executor, _, _ = executor_with_collision

    names = [t["function"]["name"] for t in executor.get_all_tools_openai_format(user_id="user-1")]

    assert len(names) == len(set(names)), f"duplicate tool names reached the model: {names}"
    assert "query_logs" in names, "the pre-existing tool must keep its raw name"
    assert "coroot__query_logs" in names, "the colliding OAuth tool must be namespaced"


def test_non_colliding_oauth_tool_keeps_its_name(executor_with_collision):
    """Namespacing must be applied only where it is needed."""
    executor, _, _ = executor_with_collision

    names = [t["function"]["name"] for t in executor.get_all_tools_openai_format(user_id="user-1")]

    assert "list_applications" in names
    assert "coroot__list_applications" not in names


def test_namespaced_oauth_tool_still_resolves(executor_with_collision):
    """Invocation and toolset lookup must work under the exposed name."""
    executor, _, mcp = executor_with_collision

    exposed = "coroot__query_logs"
    tool = executor.get_tool_by_name(exposed, user_id="user-1")

    assert tool is not None, "the model was given a name that does not resolve"
    assert tool.name == "query_logs", "the tool itself keeps the server-side name"
    assert tool.mcp_tool_name == "query_logs", "the MCP call must use the raw name"
    assert tool.toolset is mcp
    assert executor.get_toolset_name(exposed, user_id="user-1") == "coroot"


def test_existing_tool_name_still_resolves_to_its_own_tool(executor_with_collision):
    """The collision must not shadow the tool that already owned the name."""
    executor, base, mcp = executor_with_collision

    tool = executor.get_tool_by_name("query_logs", user_id="user-1")

    assert tool is base.tools[0]


def test_placeholder_is_replaced_not_duplicated(executor_with_collision):
    """The toolset's placeholder must be removed when its tools are applied."""
    executor, _, mcp = executor_with_collision
    # Simulate the auth-required state: a placeholder in the base list.
    mcp.tools = [RemoteMCPTool(name="coroot_connect", description="", parameters={}, toolset=mcp)]

    names = [t["function"]["name"] for t in executor.get_all_tools_openai_format(user_id="user-1")]

    assert "coroot_connect" not in names
    assert sorted(names) == ["coroot__query_logs", "list_applications", "query_logs"]


def test_other_users_are_unaffected(executor_with_collision):
    """Per-user tools are per user: another user sees only the base list."""
    executor, _, _ = executor_with_collision

    names = [t["function"]["name"] for t in executor.get_all_tools_openai_format(user_id="user-2")]

    assert "coroot__query_logs" not in names
    assert executor.get_tool_by_name("coroot__query_logs", user_id="user-2") is None
