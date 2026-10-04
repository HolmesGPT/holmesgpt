import os
import socket
import subprocess
import sys
import time
from unittest.mock import AsyncMock, Mock

import anyio
import httpx
import pytest
from mcp.types import CallToolResult, ListToolsResult, Tool

from tracepilot.config import MCPServerConfig, ToolPolicy
from tracepilot.mcp_gateway import MCPGateway
from tracepilot.models import Candidate, ToolSpec
from tracepilot.scenarios import demo_scope


@pytest.fixture(params=["stdio", "streamable_http"])
def gateway(request, monkeypatch):
    monkeypatch.setenv("NO_PROXY", ",".join(filter(None, [os.environ.get("NO_PROXY"), "localhost,127.0.0.1,::1"])))
    policy = {"service_traces": ToolPolicy(read_only=True, retry_safe=True)}
    process = None
    try:
        if request.param == "stdio":
            config = MCPServerConfig(command="{python}", args=["-m", "tracepilot.fixture_server"], tools=policy)
        else:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            process = subprocess.Popen([sys.executable, "-m", "tracepilot.fixture_server", "--transport", "streamable-http", "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            url = f"http://127.0.0.1:{port}/mcp"
            config = MCPServerConfig(transport="streamable_http", url=url, tools=policy)
            for _ in range(100):
                try:
                    httpx.get(url, timeout=0.1)
                    break
                except httpx.HTTPError:
                    if process.poll() is not None:
                        raise RuntimeError("MCP fixture did not start")
                    time.sleep(0.05)
            else:
                raise RuntimeError("MCP fixture startup timed out")
        yield MCPGateway({"observability": config}, demo_scope())
    finally:
        if process:
            process.terminate()
            process.wait(timeout=5)


def candidate(spec):
    return Candidate(id="trace", tool=spec.name, tool_version=spec.version, arguments=demo_scope().model_dump(mode="json"), purpose="Find slow spans", direction="request-path")


def test_real_protocol_discovery_and_invocation(gateway):
    specs = gateway.catalog()
    assert [s.name for s in specs] == ["observability__service_traces"]
    assert specs[0].output_schema
    output = gateway.execute(candidate(specs[0]))
    assert output.status == "success", output.error
    assert output.data["pod"] == "checkout-a"
    assert output.data["simulated"] is True


def test_scope_violation_never_opens_transport(gateway):
    planned = candidate(gateway.catalog()[0])
    planned.arguments["namespace"] = "private"
    gateway._operation = AsyncMock()
    output = gateway.execute(planned)
    assert output.error_kind == "permission"
    gateway._operation.assert_not_called()


def test_policy_change_blocks_stale_call(gateway):
    planned = candidate(gateway.catalog()[0])
    gateway.servers["observability"].tools["service_traces"].retry_safe = False
    assert gateway.execute(planned).status == "error"


def test_pagination_collects_pages_and_detects_cursor_loop():
    session = Mock()
    session.list_tools = AsyncMock(side_effect=[ListToolsResult(tools=[Tool(name="a", inputSchema={})], nextCursor="next"), ListToolsResult(tools=[Tool(name="b", inputSchema={})])])
    assert set(anyio.run(MCPGateway._list, session)) == {"a", "b"}
    session.list_tools = AsyncMock(side_effect=[ListToolsResult(tools=[], nextCursor="loop"), ListToolsResult(tools=[], nextCursor="loop")])
    with pytest.raises(ValueError, match="repeated"):
        anyio.run(MCPGateway._list, session)


def test_oversized_and_invalid_output():
    spec = ToolSpec(name="q", source="test", description="read", parameters={}, output_schema={"type": "object", "required": ["observations"]})
    assert MCPGateway._normalize(CallToolResult(content=[], structuredContent={"wrong": True}), spec, 1000).status == "error"
    assert "size limit" in MCPGateway._normalize(CallToolResult(content=[], structuredContent={"observations": "x" * 1000}), spec, 100).error


def test_external_schema_refs_are_not_fetched():
    gateway = MCPGateway({"source": MCPServerConfig(command="{python}", tools={"read": ToolPolicy(read_only=True)})}, demo_scope())
    with pytest.raises(ValueError, match="External schema"):
        gateway._spec("source", Tool(name="read", inputSchema={"$ref": "https://example.com/schema"}))
