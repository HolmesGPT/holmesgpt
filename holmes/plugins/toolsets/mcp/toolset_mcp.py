import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, Union

import httpx
from jinja2 import Template
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool as MCP_Tool
from pydantic import AnyUrl, Field, model_validator

from holmes.common.env_vars import SSE_READ_TIMEOUT
from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
)
from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)


def _extract_root_error_message(exc: Exception) -> str:
    """Extract the actual error message from an ExceptionGroup.

    When the MCP library's internal asyncio.TaskGroup encounters errors (e.g. auth
    failures, connection refused), the real exception gets wrapped in an
    ExceptionGroup with the unhelpful message "unhandled errors in a TaskGroup
    (1 sub-exception)".  This function unwraps the group to surface the actual
    root-cause error so that users see, for example, "401 Unauthorized" instead.
    """
    current: BaseException = exc
    while hasattr(current, "exceptions") and current.exceptions:
        current = current.exceptions[0]
    return str(current)


# Lock per MCP server URL to serialize calls to the same server
_server_locks: Dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


class CaseInsensitiveDict(dict):
    """Dictionary with case-insensitive key lookup for HTTP headers."""

    def __getitem__(self, key):
        if isinstance(key, str):
            for k, v in self.items():
                if k.lower() == key.lower():
                    return v
        raise KeyError(key)


def create_mcp_http_client_factory(verify_ssl: bool = True):
    """Create a factory function for httpx clients with configurable SSL verification."""

    def factory(
        headers: Dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        kwargs: Dict[str, Any] = {
            "follow_redirects": True,
            "verify": verify_ssl,
        }
        if timeout is None:
            kwargs["timeout"] = httpx.Timeout(SSE_READ_TIMEOUT)
        else:
            kwargs["timeout"] = timeout
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    return factory


def get_server_lock(url: str) -> threading.Lock:
    """Get or create a lock for a specific MCP server URL."""
    with _locks_lock:
        if url not in _server_locks:
            _server_locks[url] = threading.Lock()
        return _server_locks[url]


class MCPMode(str, Enum):
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"
    STDIO = "stdio"


class MCPConfig(ToolsetConfig):
    url: AnyUrl = Field(
        title="URL",
        description="MCP server URL (for SSE or Streamable HTTP modes).",
        examples=["http://example.com:8000/mcp/messages"],
    )
    mode: MCPMode = Field(
        default=MCPMode.SSE,
        title="Mode",
        description="Connection mode to use when talking to the MCP server.",
        examples=[MCPMode.STREAMABLE_HTTP],
    )
    headers: Optional[Dict[str, str]] = Field(
        default=None,
        title="Headers",
        description="Optional HTTP headers to include in requests (e.g., Authorization).",
        examples=[{"Authorization": "Bearer YOUR_TOKEN"}],
    )
    verify_ssl: bool = Field(
        default=True,
        title="Verify SSL",
        description="Whether to verify SSL certificates (set to false for local/dev servers without valid SSL).",
        examples=[False],
    )
    extra_headers: Optional[Dict[str, str]] = Field(
        default=None,
        title="Extra Headers",
        description="Template headers that will be rendered with request context and environment variables.",
        examples=[
            {
                "X-Custom-Header": "{{ request_context.headers['X-Custom-Header'] }}",
                "X-Api-Key": "{{ env.API_KEY }}",
            }
        ],
    )
    icon_url: str = Field(
        default="https://registry.npmmirror.com/@lobehub/icons-static-png/1.46.0/files/light/mcp.png",
        description="Icon URL for this MCP server, displayed in the UI for tool calls.",
        examples=["https://cdn.simpleicons.org/github/181717"],
    )

    def get_lock_string(self) -> str:
        return str(self.url)


class StdioMCPConfig(ToolsetConfig):
    mode: MCPMode = Field(
        default=MCPMode.STDIO,
        title="Mode",
        description="Stdio mode runs an MCP server as a local subprocess.",
        examples=[MCPMode.STDIO],
    )
    command: str = Field(
        title="Command",
        description="The command to start the MCP server (e.g., npx, uv, python).",
        examples=["npx"],
    )
    args: Optional[List[str]] = Field(
        default=None,
        title="Arguments",
        description="Arguments to pass to the MCP server command.",
        examples=[["-y", "@modelcontextprotocol/server-github"]],
    )
    env: Optional[Dict[str, str]] = Field(
        default=None,
        title="Environment Variables",
        description="Environment variables to set for the MCP server process.",
        examples=[{"GITHUB_PERSONAL_ACCESS_TOKEN": "{{ env.GITHUB_TOKEN }}"}],
    )
    icon_url: str = Field(
        default="https://registry.npmmirror.com/@lobehub/icons-static-png/1.46.0/files/light/mcp.png",
        description="Icon URL for this MCP server, displayed in the UI for tool calls.",
        examples=["https://cdn.simpleicons.org/github/181717"],
    )

    def get_lock_string(self) -> str:
        return str(self.command)


@asynccontextmanager
async def get_initialized_mcp_session(
    toolset: "RemoteMCPToolset", request_context: Optional[Dict[str, Any]] = None
):
    if toolset._mcp_config is None:
        raise ValueError("MCP config is not initialized")

    if isinstance(toolset._mcp_config, StdioMCPConfig):
        server_params = StdioServerParameters(
            command=toolset._mcp_config.command,
            args=toolset._mcp_config.args or [],
            env=toolset._mcp_config.env,
        )
        async with stdio_client(server_params) as (
            read_stream,
            write_stream,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                _ = await session.initialize()
                yield session
    elif toolset._mcp_config.mode == MCPMode.SSE:
        url = str(toolset._mcp_config.url)
        httpx_factory = create_mcp_http_client_factory(toolset._mcp_config.verify_ssl)
        rendered_headers = toolset._render_headers(request_context)
        async with sse_client(
            url,
            rendered_headers,
            sse_read_timeout=SSE_READ_TIMEOUT,
            httpx_client_factory=httpx_factory,
        ) as (
            read_stream,
            write_stream,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                _ = await session.initialize()
                yield session
    else:
        url = str(toolset._mcp_config.url)
        httpx_factory = create_mcp_http_client_factory(toolset._mcp_config.verify_ssl)
        rendered_headers = toolset._render_headers(request_context)
        async with streamablehttp_client(
            url,
            headers=rendered_headers,
            sse_read_timeout=SSE_READ_TIMEOUT,
            httpx_client_factory=httpx_factory,
        ) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                _ = await session.initialize()
                yield session


class RemoteMCPTool(Tool):
    toolset: "RemoteMCPToolset" = Field(exclude=True)

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            # Serialize calls to the same MCP server to prevent SSE conflicts
            # Different servers can still run in parallel
            if not self.toolset._mcp_config:
                raise ValueError("MCP config not initialized")

            lock = get_server_lock(str(self.toolset._mcp_config.get_lock_string()))
            with lock:
                return asyncio.run(self._invoke_async(params, context.request_context))
        except Exception as e:
            error_detail = _extract_root_error_message(e)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_detail,
                params=params,
                invocation=f"MCPtool {self.name} with params {params}",
            )

    @staticmethod
    def _is_content_error(content: str) -> bool:
        try:  # aws mcp sometimes returns an error in content - status code != 200
            json_content: dict = json.loads(content)
            status_code = json_content.get("response", {}).get("status_code", 200)
            return status_code >= 300
        except Exception:
            return False

    async def _invoke_async(
        self, params: Dict, request_context: Optional[Dict[str, Any]]
    ) -> StructuredToolResult:
        async with get_initialized_mcp_session(
            self.toolset, request_context
        ) as session:
            tool_result = await session.call_tool(self.name, params)

        merged_text = " ".join(c.text for c in tool_result.content if c.type == "text")
        return StructuredToolResult(
            status=(
                StructuredToolResultStatus.ERROR
                if (tool_result.isError or self._is_content_error(merged_text))
                else StructuredToolResultStatus.SUCCESS
            ),
            data=merged_text,
            params=params,
            invocation=f"MCPtool {self.name} with params {params}",
        )

    @classmethod
    def create(
        cls,
        tool: MCP_Tool,
        toolset: "RemoteMCPToolset",
    ):
        parameters = cls.parse_input_schema(tool.inputSchema)
        return cls(
            name=tool.name,
            description=tool.description or "",
            parameters=parameters,
            toolset=toolset,
        )

    @classmethod
    def parse_input_schema(
        cls, input_schema: dict[str, Any]
    ) -> Dict[str, ToolParameter]:
        required_list = input_schema.get("required", [])
        schema_params = input_schema.get("properties", {})
        parameters = {}
        for key, val in schema_params.items():
            parameters[key] = ToolParameter(
                description=val.get("description"),
                type=val.get("type", "string"),
                required=key in required_list,
            )

        return parameters

    def get_parameterized_one_liner(self, params: Dict) -> str:
        # AWS MCP cli_command
        if params and params.get("cli_command"):
            return f"{params.get('cli_command')}"

        # gcloud MCP run_gcloud_command
        if self.name == "run_gcloud_command" and params and "args" in params:
            args = params.get("args", [])
            if isinstance(args, list):
                return f"gcloud {' '.join(str(arg) for arg in args)}"

        if self.name and params and "args" in params:
            args = params.get("args", [])
            if isinstance(args, list):
                return f"{self.name} {' '.join(str(arg) for arg in args)}"

        return f"{self.toolset.name}: {self.name} {params}"


class RemoteMCPToolset(Toolset):
    config_classes: ClassVar[list[Type[Union[MCPConfig, StdioMCPConfig]]]] = [
        MCPConfig,
        StdioMCPConfig,
    ]
    tools: List[RemoteMCPTool] = Field(default_factory=list)  # type: ignore
    _mcp_config: Optional[Union[MCPConfig, StdioMCPConfig]] = None

    def _render_headers(
        self, request_context: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, str]]:
        """
        Merge and render headers for MCP connection.

        Process:
        1. Start with 'headers' field (backward compatibility, passed as-is)
        2. Render 'extra_headers' templates with request_context and env vars
        3. Merge them (extra_headers takes precedence)

        Template sources for extra_headers:
        - {{ request_context.headers['foo'] }}: Pass-through from client request
        - {{ env.CORALOGIX_API_KEY }}: From environment variables
        - "hardcoded value": Static hardcoded values

        Returns:
            Merged headers dictionary or None

        Example of mcp_config:
            mcp_servers:
                my_mcp_server:
                    config:
                        ...
                        headers:
                            Header-Name: "hardcoded value"
                        extra_headers:
                            Header-Name-1: "hardcoded value"
                            Header-Name-2: "{{ request_context.headers['foo'] }}"
                            Header-Name-3: "{{ env.CORALOGIX_API_KEY }}"
        """
        if not isinstance(self._mcp_config, MCPConfig):
            return None

        # Start with direct headers (no rendering, backward compatibility)
        final_headers = {}
        if self._mcp_config.headers:
            final_headers.update(self._mcp_config.headers)

        # Render and merge extra_headers
        if self._mcp_config.extra_headers:
            for header_name, header_template in self._mcp_config.extra_headers.items():
                try:
                    rendered_value = self._render_template(
                        header_template, request_context
                    )
                    final_headers[header_name] = rendered_value
                except Exception as e:  # noqa: BLE001
                    logging.warning(
                        f"MCP toolset '{self.name}': Failed to render header template "
                        f"'{header_name}': {e}"
                    )

        return final_headers if final_headers else None

    def _render_template(
        self, template_str: str, request_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Render a single template string using Jinja2.

        Supports:
        - {{ request_context.headers['foo'] }} - case-insensitive header lookup
        - {{ env.API_KEY }} - environment variables
        - Plain strings (no template syntax)
        """
        # Build context for Jinja2 template rendering
        context: Dict[str, Any] = {
            "env": os.environ,
        }

        if request_context:
            # Wrap headers in CaseInsensitiveDict for case-insensitive lookup
            request_context_copy = request_context.copy()
            if "headers" in request_context_copy:
                request_context_copy["headers"] = CaseInsensitiveDict(
                    request_context_copy["headers"]
                )
            context["request_context"] = request_context_copy
        else:
            context["request_context"] = {"headers": CaseInsensitiveDict()}

        try:
            template = Template(template_str)
            return template.render(context)
        except Exception as e:
            logging.warning(
                f"MCP toolset '{self.name}': Failed to render template '{template_str}': {e}"
            )
            return template_str

    def model_post_init(self, __context: Any) -> None:
        self.prerequisites = [
            CallablePrerequisite(callable=self.prerequisites_callable)
        ]
        # Set icon from config if specified
        if self.icon_url is None and self.config:
            self.icon_url = self.config.get("icon_url")

    @model_validator(mode="before")
    @classmethod
    def migrate_url_to_config(cls, values: dict[str, Any]) -> dict[str, Any]:
        """
        Migrates url from field parameter to config object.
        If url is passed as a parameter, it's moved to config (or config is created if it doesn't exist).
        """
        if not isinstance(values, dict) or "url" not in values:
            return values

        url_value = values.pop("url")
        if url_value is None:
            return values

        config = values.get("config")
        if config is None:
            config = {}
            values["config"] = config

        toolset_name = values.get("name", "unknown")
        if "url" in config:
            logging.warning(
                f"Toolset {toolset_name}: has two urls defined, remove the 'url' field from the toolset configuration and keep the 'url' in the config section."
            )
            return values

        logging.warning(
            f"Toolset {toolset_name}: 'url' field has been migrated to config. "
            "Please move 'url' to the config section."
        )
        config["url"] = url_value
        return values

    def prerequisites_callable(self, config) -> Tuple[bool, str]:
        try:
            if not config:
                return (False, f"Config is required for {self.name}")

            mode_value = config.get("mode", MCPMode.SSE.value)
            allowed_modes = [e.value for e in MCPMode]
            if mode_value not in allowed_modes:
                return (
                    False,
                    f'Invalid mode "{mode_value}", allowed modes are {", ".join(allowed_modes)}',
                )

            if mode_value == MCPMode.STDIO.value:
                self._mcp_config = StdioMCPConfig(**config)
            else:
                self._mcp_config = MCPConfig(**config)
                clean_url_str = str(self._mcp_config.url).rstrip("/")

                if self._mcp_config.mode == MCPMode.SSE and not clean_url_str.endswith(
                    "/sse"
                ):
                    self._mcp_config.url = AnyUrl(clean_url_str + "/sse")

            tools_result = asyncio.run(self._get_server_tools())

            self.tools = [
                RemoteMCPTool.create(tool, self) for tool in tools_result.tools
            ]

            if not self.tools:
                logging.warning(f"mcp server {self.name} loaded 0 tools.")

            return (True, "")
        except Exception as e:
            error_detail = _extract_root_error_message(e)
            return (
                False,
                f"Failed to load mcp server {self.name}: {error_detail}"
                ". If the server is still starting up, Holmes will retry automatically",
            )

    async def _get_server_tools(self):
        async with get_initialized_mcp_session(self, None) as session:
            return await session.list_tools()
