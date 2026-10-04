"""Direct MCP SDK adapter. Each operation owns and closes its session."""

import os
import sys
from datetime import timedelta
from typing import Any

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool

from tracepilot.config import MCPServerConfig
from tracepilot.gateway import tool_error_output
from tracepilot.models import Candidate, InvestigationScope, ToolOutput, ToolSpec, fingerprint
from tracepilot.validation import validator


def required_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value.strip():
        raise ValueError(f"Required environment variable is missing: {name}")
    return value


class MCPGateway:
    def __init__(self, servers: dict[str, MCPServerConfig], scope: InvestigationScope):
        self.servers = servers
        self.scope = scope
        self._catalog: list[ToolSpec] = []
        self._routes: dict[str, tuple[str, str]] = {}

    def _spec(self, server_name: str, tool: Tool) -> ToolSpec:
        server = self.servers[server_name]
        policy = server.tools[tool.name]
        if len(tool.model_dump_json()) > 32000:
            raise ValueError("MCP tool definition exceeds 32,000 characters")
        identity = fingerprint({"server": server.model_dump(), "scope": self.scope.model_dump(mode="json")})
        validator(tool.inputSchema)
        if tool.outputSchema is not None:
            validator(tool.outputSchema)
        return ToolSpec(name=f"{server_name}__{tool.name}", description=tool.description or tool.name,
                        parameters=tool.inputSchema, output_schema=tool.outputSchema,
                        source=f"mcp:{server_name}:{identity[:16]}", retry_safe=policy.retry_safe,
                        approval_required=policy.approval_required)

    @staticmethod
    async def _list(session: ClientSession) -> dict[str, Tool]:
        tools: dict[str, Tool] = {}
        cursor = None
        seen_cursors: set[str] = set()
        for _ in range(20):
            page = await session.list_tools(cursor=cursor)
            for tool in page.tools:
                if tool.name in tools:
                    raise ValueError("MCP server returned duplicate tool names")
                tools[tool.name] = tool
            if len(tools) > 1000:
                raise ValueError("MCP catalog is too large")
            cursor = page.nextCursor
            if not cursor:
                return tools
            if cursor in seen_cursors:
                raise ValueError("MCP server returned a repeated pagination cursor")
            seen_cursors.add(cursor)
        raise ValueError("MCP catalog exceeded page limit")

    async def _session_operation(self, server_name: str, session: ClientSession, candidate: Candidate | None) -> list[ToolSpec] | ToolOutput:
        await session.initialize()
        listed = await self._list(session)
        server = self.servers[server_name]
        specs = {name: self._spec(server_name, tool) for name, tool in listed.items() if name in server.tools}
        if candidate is None:
            return list(specs.values())
        route = self._routes.get(candidate.tool)
        if route is None or route[0] != server_name or route[1] not in specs:
            raise PermissionError("MCP tool is no longer available to this session")
        remote_name = route[1]
        spec = specs[remote_name]
        if spec.version != candidate.tool_version:
            raise ValueError("MCP schema or local policy changed before invocation")
        policy = server.tools[remote_name]
        if policy.approval_required:
            return ToolOutput(status="approval_required", error="Operator approval is required")
        policy.scope.validate_arguments(candidate.arguments, self.scope)
        validator(spec.parameters).validate(candidate.arguments)
        result = await session.call_tool(remote_name, candidate.arguments,
                                         read_timeout_seconds=timedelta(seconds=server.timeout_seconds))
        return self._normalize(result, spec, server.max_result_chars)

    @staticmethod
    def _normalize(result: CallToolResult, spec: ToolSpec, max_chars: int) -> ToolOutput:
        if len(result.model_dump_json()) > max_chars:
            return ToolOutput(status="error", error="MCP result exceeds the configured size limit; narrow the query")
        content: Any = result.structuredContent
        if content is None:
            content = {"content": [item.model_dump(mode="json") for item in result.content]}
        if result.isError:
            return ToolOutput(status="error", data=content, error="MCP tool returned isError", error_kind="unknown")
        if spec.output_schema is not None:
            try:
                validator(spec.output_schema).validate(content)
            except Exception:
                return ToolOutput(status="error", error="MCP result failed its output schema", error_kind="unknown")
        return ToolOutput(status="success", data=content)

    async def _operation(self, server_name: str, candidate: Candidate | None) -> list[ToolSpec] | ToolOutput:
        server = self.servers[server_name]
        with anyio.fail_after(server.timeout_seconds):
            if server.transport == "stdio":
                params = StdioServerParameters(command=sys.executable if server.command == "{python}" else server.command,
                                               args=server.args, env={n: required_environment(n) for n in server.env_names})
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=server.timeout_seconds)) as session:
                        return await self._session_operation(server_name, session, candidate)
            else:
                headers = {header: required_environment(name) for header, name in server.headers_env.items()}
                async with httpx.AsyncClient(headers=headers, timeout=server.timeout_seconds, follow_redirects=False) as client:
                    async with streamable_http_client(server.url, http_client=client) as (read, write, _):
                        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=server.timeout_seconds)) as session:
                            return await self._session_operation(server_name, session, candidate)

    def catalog(self, refresh: bool = True) -> list[ToolSpec]:
        if refresh or not self._catalog:
            specs: list[ToolSpec] = []
            routes: dict[str, tuple[str, str]] = {}
            for name, server in self.servers.items():
                items = anyio.run(self._operation, name, None)
                assert isinstance(items, list)
                specs.extend(items)
                for remote in server.tools:
                    routes[f"{name}__{remote}"] = (name, remote)
            if len({spec.name for spec in specs}) != len(specs):
                raise ValueError("MCP tool namespace collision")
            self._catalog, self._routes = specs, routes
        return list(self._catalog)

    def execute(self, candidate: Candidate) -> ToolOutput:
        route = self._routes.get(candidate.tool)
        if route is None:
            return ToolOutput(status="approval_required", error="Tool is outside the local allowlist")
        server_name, remote_name = route
        try:
            policy = self.servers[server_name].tools[remote_name]
            if policy.approval_required:
                return ToolOutput(status="approval_required", error="Operator approval is required")
            policy.scope.validate_arguments(candidate.arguments, self.scope)
            result = anyio.run(self._operation, server_name, candidate)
            assert isinstance(result, ToolOutput)
            return result
        except Exception as exc:
            return tool_error_output(exc)
