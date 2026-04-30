import asyncio
import base64
import binascii
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, TextIO, Tuple, Type, Union
from urllib.parse import urlparse

import httpx
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool as MCP_Tool
from pydantic import AnyUrl, BaseModel, Field, model_validator

from holmes.common.env_vars import MCP_TOOL_CALL_TIMEOUT_SEC, SSE_READ_TIMEOUT
from holmes.core.oauth_config import (
    MCPOAuthConfig,
    OAuthEndpoints,
    OAuthTokenExchangeError,
    _get_exchange_manager,
)
from holmes.core.oauth_utils import (
    _get_token_manager,
    cli_oauth_flow,
    discover_auth_server_from_prm,
    fetch_oauth_metadata,
    generate_pkce,
)
from holmes.core.config import config_path_dir
from holmes.core.tools import (
    ApprovalRequirement,
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetType,
)
from holmes.plugins.toolsets.mcp.oauth_token_manager import _get_user_id
from holmes.utils.header_rendering import render_header_templates
from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)
display_logger = logging.getLogger("holmes.display.mcp_toolset")


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
    _name: ClassVar[Optional[str]] = "HTTP/SSE"
    _description: ClassVar[Optional[str]] = "Connect via HTTP using SSE or Streamable HTTP transport"

    mode: MCPMode = Field(
        default=MCPMode.SSE,
        title="Mode",
        description="Connection mode to use when talking to the MCP server.",
        examples=[MCPMode.STREAMABLE_HTTP],
    )
    url: AnyUrl = Field(
        title="URL",
        description="MCP server URL (for SSE or Streamable HTTP modes).",
        examples=["http://example.com:8000/mcp/messages"],
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
    oauth: Optional[MCPOAuthConfig] = Field(
        default=None,
        title="OAuth",
        description="OAuth authorization_code configuration. When set, users authenticate via browser before tools can be used.",
    )

    def get_lock_string(self) -> str:
        return str(self.url)


class StdioMCPConfig(ToolsetConfig):
    _name: ClassVar[Optional[str]] = "Stdio"
    _description: ClassVar[Optional[str]] = "Run MCP server as a local subprocess using stdio transport"

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


def _get_mcp_log_file(server_name: str) -> TextIO:
    """Get a file handle for MCP server stderr output.

    Redirects MCP subprocess stderr to ~/.holmes/logs/mcp/<server_name>.log
    so it doesn't pollute the CLI output.
    """
    log_dir = os.path.join(config_path_dir, "logs", "mcp")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{server_name}.log")
    display_logger.info(f"MCP server '{server_name}' logs: {log_path}")
    return open(log_path, "w")



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
        errlog = _get_mcp_log_file(toolset.name)
        try:
            async with stdio_client(server_params, errlog=errlog) as (
                read_stream,
                write_stream,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    _ = await session.initialize()
                    yield session
        finally:
            errlog.close()
    elif toolset._mcp_config.mode == MCPMode.SSE:
        url = str(toolset._mcp_config.url)
        httpx_factory = create_mcp_http_client_factory(toolset._mcp_config.verify_ssl)
        rendered_headers = toolset._render_headers(request_context)
        async with sse_client(
            url,
            rendered_headers,
            sse_read_timeout=MCP_TOOL_CALL_TIMEOUT_SEC,
            httpx_client_factory=httpx_factory,
        ) as (
            read_stream,
            write_stream,
        ):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=MCP_TOOL_CALL_TIMEOUT_SEC),
            ) as session:
                _ = await session.initialize()
                yield session
    else:
        url = str(toolset._mcp_config.url)
        httpx_factory = create_mcp_http_client_factory(toolset._mcp_config.verify_ssl)
        rendered_headers = toolset._render_headers(request_context)
        async with streamablehttp_client(
            url,
            headers=rendered_headers,
            sse_read_timeout=MCP_TOOL_CALL_TIMEOUT_SEC,
            httpx_client_factory=httpx_factory,
        ) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=MCP_TOOL_CALL_TIMEOUT_SEC),
            ) as session:
                _ = await session.initialize()
                yield session


class RemoteMCPTool(Tool):
    toolset: "RemoteMCPToolset" = Field(exclude=True)

    def requires_approval(
        self, params: Dict, context: ToolInvokeContext
    ) -> Optional[ApprovalRequirement]:
        """Prompt user for OAuth browser login when no cached token exists."""
        if not self.toolset.is_oauth_enabled:
            return None

        oauth_config = self.toolset._mcp_config.oauth
        disk_key = str(self.toolset._mcp_config.url) if isinstance(self.toolset._mcp_config, MCPConfig) else None

        # Try to get a token from cache → refresh → DB → disk
        mgr = _get_token_manager()
        token = mgr.get_access_token(oauth_config, context.request_context, disk_key=disk_key)
        if token:
            logger.info("OAuth MCP %s: token available via manager", self.toolset.name)
            return None

        # No token found anywhere — need to authenticate
        user_id = _get_user_id(context.request_context)

        # CLI mode: no request_context means the call came from the CLI, not the API server
        is_cli = context.request_context is None
        if is_cli:
            # CLI mode: run browser OAuth flow synchronously
            logger.info("OAuth MCP %s: CLI mode, running browser OAuth flow", self.toolset.name)
            oauth_endpoints = OAuthEndpoints(
                authorization_url=oauth_config.authorization_url,
                token_url=oauth_config.token_url,
                client_id=oauth_config.client_id,
                scopes=oauth_config.scopes,
                registration_endpoint=oauth_config.registration_endpoint,
            )
            token_data = cli_oauth_flow(oauth_endpoints, self.toolset.name)
            if token_data:
                _get_token_manager().store_token(
                    oauth_config, token_data, context.request_context,
                    disk_key=disk_key, store_to_disk=True,
                )
                logger.info("OAuth MCP %s: CLI auth successful", self.toolset.name)
                return None  # Token obtained, no approval needed
            else:
                logger.warning("OAuth MCP %s: CLI OAuth flow failed", self.toolset.name)
                # Fall through to frontend flow as fallback

        # Frontend mode: use PKCE + approval mechanism
        code_verifier, code_challenge = generate_pkce()

        _get_exchange_manager().register_pending(
            tool_call_id=context.tool_call_id,
            code_verifier=code_verifier,
            oauth_config=oauth_config,
        )

        metadata: Dict[str, Any] = {
            "authorization_url": oauth_config.authorization_url,
            "client_id": oauth_config.client_id,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if oauth_config.scopes:
            metadata["scopes"] = oauth_config.scopes
        if oauth_config.registration_endpoint:
            metadata["registration_endpoint"] = oauth_config.registration_endpoint
        params["__oauth_metadata"] = metadata

        return ApprovalRequirement(
            needs_approval=True,
            reason=f"OAuth authentication required for MCP server '{self.toolset.name}'",
        )

    def _is_placeholder_connect_tool(self) -> bool:
        return self.name == self.toolset.connect_tool_name

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            # For OAuth placeholder tools: load real tools after authentication
            if self._is_placeholder_connect_tool():
                return self._invoke_oauth_connect(params, context)

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

    def _invoke_oauth_connect(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Handle the OAuth placeholder tool: load real tools from the MCP server after authentication."""
        try:
            if not self.toolset._mcp_config:
                raise ValueError("MCP config not initialized")

            lock = get_server_lock(str(self.toolset._mcp_config.get_lock_string()))
            with lock:
                tools_result = asyncio.run(self.toolset._get_server_tools_with_context(context.request_context))

            real_tools = [RemoteMCPTool.create(tool, self.toolset) for tool in tools_result.tools]

            if real_tools:
                tool_names = [t.name for t in real_tools]
                logger.info("OAuth MCP %s: loaded %d tools after authentication: %s", self.toolset.name, len(real_tools), tool_names)
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=f"Successfully authenticated and discovered {len(real_tools)} tools: {', '.join(tool_names)}. You can now call these tools directly.",
                    params=params,
                    invocation=f"OAuth connect to {self.toolset.name}",
                    oauth_tools=real_tools,
                )
            else:
                logger.warning("OAuth MCP %s: authenticated but no tools found", self.toolset.name)
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Authenticated but no tools found on MCP server {self.toolset.name}",
                    params=params,
                    invocation=f"OAuth connect to {self.toolset.name}",
                )
        except Exception as e:
            error_detail = _extract_root_error_message(e)
            logger.warning("OAuth MCP %s: connect failed: %s", self.toolset.name, error_detail)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"OAuth connect failed: {error_detail}",
                params=params,
                invocation=f"OAuth connect to {self.toolset.name}",
            )

    @staticmethod
    def _is_content_error(content: str) -> bool:
        try:  # aws mcp sometimes returns an error in content - status code != 200
            json_content: dict = json.loads(content)
            status_code = json_content.get("response", {}).get("status_code", 200)
            return status_code >= 300
        except Exception:
            return False

    @staticmethod
    def _extract_text_from_content_block(block: Any) -> str:
        """Extract text from any MCP content block type.

        TextContent: trivial passthrough.
        EmbeddedResource (type="resource"): pulls text from TextResourceContents,
        or base64-decodes BlobResourceContents when the mimeType indicates text.
        Without this, tools like github's get_file_contents — which return the
        actual file body inside an EmbeddedResource — appear to succeed but
        deliver nothing usable to the LLM.
        ResourceLink (type="resource_link"): surfaces the URI as a hint.
        """
        block_type = getattr(block, "type", None)
        if block_type == "text":
            return getattr(block, "text", "") or ""
        if block_type == "resource":
            resource = getattr(block, "resource", None)
            if resource is None:
                return ""
            text = getattr(resource, "text", None)
            if text is not None:
                return text
            blob = getattr(resource, "blob", None)
            if blob is None:
                return ""
            mime = getattr(resource, "mimeType", "") or ""
            uri = getattr(resource, "uri", "")
            if (
                mime.startswith("text/")
                or mime in ("application/json", "application/xml")
                or mime.endswith("+json")
                or mime.endswith("+xml")
            ):
                try:
                    return base64.b64decode(blob).decode("utf-8", errors="replace")
                except (binascii.Error, ValueError):
                    pass
            return f"[binary resource uri={uri} mimeType={mime} base64_size={len(blob)}]"
        if block_type == "resource_link":
            uri = getattr(block, "uri", "")
            name = getattr(block, "name", "") or ""
            title = getattr(block, "title", "") or ""
            label = title or name
            return f"[resource_link {label}: {uri}]" if label else f"[resource_link: {uri}]"
        return ""

    async def _invoke_async(
        self, params: Dict, request_context: Optional[Dict[str, Any]]
    ) -> StructuredToolResult:
        async with get_initialized_mcp_session(
            self.toolset, request_context
        ) as session:
            tool_result = await session.call_tool(self.name, params)

        text_chunks = [
            self._extract_text_from_content_block(c) for c in tool_result.content
        ]
        merged_text = " ".join(t for t in text_chunks if t)

        is_error = tool_result.isError or self._is_content_error(merged_text)

        images = None
        if not is_error:
            images = [
                {"data": c.data, "mimeType": c.mimeType}
                for c in tool_result.content
                if c.type == "image"
            ] or None

        return StructuredToolResult(
            status=(
                StructuredToolResultStatus.ERROR
                if is_error
                else StructuredToolResultStatus.SUCCESS
            ),
            data=merged_text,
            images=images,
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
            parameters[key] = cls._parse_tool_parameter(
                val, root_schema=input_schema, required=key in required_list
            )

        return parameters

    @classmethod
    def _resolve_schema(
        cls, schema: dict[str, Any], root_schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolves $ref and extracts the first non-null type from anyOf/oneOf/allOf."""
        if not isinstance(schema, dict):
            return schema

        # 1. Resolve $ref
        if "$ref" in schema:
            ref_path = str(schema["$ref"])
            if ref_path.startswith("#/"):
                parts = ref_path[2:].split("/")
                resolved = root_schema
                for part in parts:
                    if isinstance(resolved, dict):
                        resolved = resolved.get(part, {})
                    else:
                        resolved = {}
                        break

                # Recursively resolve the matched definition in case it contains more refs/anyOf
                resolved_schema = dict(schema)
                resolved_schema.pop("$ref")
                resolved_schema.update(cls._resolve_schema(resolved, root_schema))
                return resolved_schema

        # 2. Handle anyOf / oneOf / allOf for nullable or union types
        for compound_key in ["anyOf", "oneOf", "allOf"]:
            if compound_key in schema and isinstance(schema[compound_key], list):
                if compound_key == "allOf":
                    merged = dict(schema)
                    merged.pop(compound_key)
                    for sub_schema in schema[compound_key]:
                        if isinstance(sub_schema, dict):
                            resolved_sub = cls._resolve_schema(sub_schema, root_schema)
                            if resolved_sub.get("type") != "null":
                                for k, v in resolved_sub.items():
                                    if k == "properties" and isinstance(v, dict):
                                        merged.setdefault("properties", {}).update(v)
                                    elif k == "required" and isinstance(v, list):
                                        reqs = merged.setdefault("required", [])
                                        for req in v:
                                            if req not in reqs:
                                                reqs.append(req)
                                    elif k == "type":
                                        if (
                                            "type" not in merged
                                            or merged["type"] == "null"
                                        ):
                                            merged["type"] = v
                                    else:
                                        merged[k] = v
                    return merged
                else:
                    # Resolve $ref inside each branch, then decide whether to
                    # flatten (single non-null branch = nullable shorthand) or
                    # preserve the full union (multiple non-null branches).
                    resolved_branches: list[dict] = []
                    has_null = False
                    for sub_schema in schema[compound_key]:
                        if isinstance(sub_schema, dict):
                            resolved_sub = cls._resolve_schema(sub_schema, root_schema)
                            if resolved_sub.get("type") == "null":
                                has_null = True
                            else:
                                resolved_branches.append(resolved_sub)

                    if len(resolved_branches) == 1:
                        # Nullable shorthand: anyOf[<type>, null] → collapse to the single type.
                        # The nullable flag is handled downstream via required=False / type list.
                        merged = dict(schema)
                        merged.pop(compound_key)
                        merged.update(resolved_branches[0])
                        return merged
                    elif len(resolved_branches) > 1:
                        # True union — preserve as anyOf so _parse_tool_parameter
                        # can populate ToolParameter.any_of.  Convert oneOf → anyOf
                        # since OpenAI strict mode supports anyOf but not oneOf.
                        merged = dict(schema)
                        merged.pop(compound_key)
                        branches = resolved_branches
                        if has_null:
                            branches = resolved_branches + [{"type": "null"}]
                        merged["anyOf"] = branches
                        return merged

        return schema

    @classmethod
    def _parse_tool_parameter(
        cls, schema: dict[str, Any], root_schema: dict[str, Any], required: bool = True
    ) -> ToolParameter:
        """Recursively parse a JSON Schema property into a ToolParameter.

        This preserves nested items, properties, and enum from MCP tool schemas
        so that the OpenAI-formatted schema sent to the LLM accurately describes
        complex parameter types (arrays, objects).
        """
        schema = cls._resolve_schema(schema, root_schema)

        # If _resolve_schema preserved a multi-branch anyOf, parse each branch
        # into a ToolParameter and store on the any_of field.
        any_of_params = None
        if "anyOf" in schema and isinstance(schema["anyOf"], list):
            branches = schema["anyOf"]
            non_null = [
                b for b in branches if isinstance(b, dict) and b.get("type") != "null"
            ]
            if len(non_null) > 1:
                # True union — parse each branch as a ToolParameter
                has_null = any(
                    isinstance(b, dict) and b.get("type") == "null" for b in branches
                )
                any_of_params = [
                    cls._parse_tool_parameter(branch, root_schema, required=True)
                    for branch in non_null
                ]
                # Use a placeholder type; type_to_open_ai_schema will use any_of instead
                return ToolParameter(
                    description=schema.get("description"),
                    type="anyOf",
                    required=required if not has_null else False,
                    any_of=any_of_params,
                    json_schema_extra={
                        k: v for k, v in schema.items() if k in {"default"}
                    }
                    or None,
                )

        param_type = schema.get("type", "string")

        items = None
        if "items" in schema and isinstance(schema["items"], dict):
            items = cls._parse_tool_parameter(
                schema["items"], root_schema, required=True
            )

        properties = None
        if "properties" in schema and isinstance(schema["properties"], dict):
            nested_required = schema.get("required", [])
            properties = {
                name: cls._parse_tool_parameter(
                    prop, root_schema, required=name in nested_required
                )
                for name, prop in schema["properties"].items()
            }

        enum = schema.get("enum")

        additional_properties = None
        raw_ap = schema.get("additionalProperties")
        if raw_ap is not None:
            if isinstance(raw_ap, bool):
                additional_properties = raw_ap
            elif isinstance(raw_ap, dict):
                # Resolve $ref pointers so the LLM sees concrete types, but
                # preserve compound keywords (anyOf/oneOf) intact — _resolve_schema
                # collapses those to a single branch which loses type information
                # (e.g. string|array becomes just string).
                if "$ref" in raw_ap:
                    additional_properties = cls._resolve_schema(raw_ap, root_schema)
                else:
                    additional_properties = raw_ap

        # Capture JSON Schema validation keywords that aren't modeled as
        # dedicated ToolParameter fields.  These are passed through to the
        # OpenAI-formatted schema so the LLM sees constraints like array
        # length limits, numeric ranges, and string patterns.
        _PASSTHROUGH_KEYWORDS = {
            "minItems",
            "maxItems",
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "minLength",
            "maxLength",
            "pattern",
            "default",
        }
        json_schema_extra = {
            k: v for k, v in schema.items() if k in _PASSTHROUGH_KEYWORDS
        }

        return ToolParameter(
            description=schema.get("description"),
            type=param_type,
            required=required,
            items=items,
            properties=properties,
            enum=enum,
            additional_properties=additional_properties,
            json_schema_extra=json_schema_extra or None,
        )

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
    description: str = "MCP server toolset"
    tools: List[RemoteMCPTool] = Field(default_factory=list)  # type: ignore
    _mcp_config: Optional[Union[MCPConfig, StdioMCPConfig]] = None

    @property
    def is_oauth_enabled(self) -> bool:
        return isinstance(self._mcp_config, MCPConfig) and bool(self._mcp_config.oauth) and self._mcp_config.oauth.enabled

    @property
    def connect_tool_name(self) -> str:
        """The name of the OAuth placeholder tool for this MCP server."""
        return f"{self.name}_connect"

    def get_oauth_config(self) -> Optional[Dict[str, Any]]:
        """Return OAuth config dict for syncing to DB/frontend, or None if not OAuth-enabled."""
        if not self.is_oauth_enabled or not isinstance(self._mcp_config, MCPConfig) or not self._mcp_config.oauth:
            return None
        return self._mcp_config.oauth.model_dump(exclude_none=True)

    def _load_remote_tools(self, request_context: Optional[Dict[str, Any]] = None) -> List["RemoteMCPTool"]:
        """Load tools from the MCP server and return as RemoteMCPTool instances."""
        if request_context:
            tools_result = asyncio.run(self._get_server_tools_with_context(request_context))
        else:
            tools_result = asyncio.run(self._get_server_tools())
        return [RemoteMCPTool.create(tool, self) for tool in tools_result.tools]

    def _render_headers(
        self, request_context: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, str]]:
        """
        Merge and render headers for MCP connection.

        Process:
        1. Start with 'headers' field (backward compatibility, passed as-is)
        2. Render 'extra_headers' via Jinja2 templates
        3. Inject OAuth Bearer token if this is an OAuth-enabled toolset
        4. Merge them (later layers take precedence)

        Returns:
            Merged headers dictionary or None
        """
        if not isinstance(self._mcp_config, MCPConfig):
            return None

        # Start with direct headers (no rendering, backward compatibility)
        final_headers: Dict[str, str] = {}
        if self._mcp_config.headers:
            final_headers.update(self._mcp_config.headers)

        # Render and merge config-level extra_headers
        if self._mcp_config.extra_headers:
            rendered = render_header_templates(
                extra_headers=self._mcp_config.extra_headers,
                request_context=request_context,
                source_name=self.name,
            )
            if rendered:
                final_headers.update(rendered)

        # Inject OAuth Bearer token if available (only when authorization_url is
        # known — before discovery it's None and we can't look up a token yet)
        if self.is_oauth_enabled and self._mcp_config.oauth.authorization_url:
            oauth_config = self._mcp_config.oauth
            cached_token = _get_token_manager().get_access_token(oauth_config, request_context)
            if cached_token:
                final_headers["Authorization"] = f"Bearer {cached_token}"
                logger.debug("OAuth token injected for MCP server %s", self.name)
            else:
                logger.warning("OAuth MCP server %s: no cached token — request will likely 401", self.name)

        return final_headers if final_headers else None

    def model_post_init(self, __context: Any) -> None:
        self.type = ToolsetType.MCP
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

            # For OAuth-protected servers, skip full MCP session init (it will 401).
            # Just verify the server is reachable and register a placeholder tool
            # that triggers the OAuth flow on first use. Tools are loaded after auth.
            if self.is_oauth_enabled:
                return self._check_oauth_server_reachable()

            self.tools = self._load_remote_tools()

            if not self.tools:
                logging.warning("mcp server %s loaded 0 tools.", self.name)

            return (True, "")
        except Exception as e:
            error_detail = _extract_root_error_message(e)
            return (
                False,
                f"Failed to load mcp server {self.name}: {error_detail}"
                ". If the server is still starting up, Holmes will retry automatically",
            )

    def _check_oauth_server_reachable(self) -> Tuple[bool, str]:
        """For OAuth MCP servers, verify reachability without authenticating.

        If a cached token exists (from a previous request in the same conversation),
        load the real tools directly. Otherwise, auto-discover OAuth endpoints if needed,
        then register a placeholder tool that triggers the OAuth flow on first use.
        """
        if not isinstance(self._mcp_config, MCPConfig) or self._mcp_config.oauth is None:
            return (False, f"MCP server {self.name}: OAuth enabled but config not properly initialized")
        url = str(self._mcp_config.url).rstrip("/")
        oauth_config = self._mcp_config.oauth

        try:
            # Collect HTTP responses for PRM discovery.  The WWW-Authenticate header
            # (typically on a 401) contains the resource_metadata URL needed to find
            # the authorization server.  We try two endpoints and pick the best
            # response — preferring one with a WWW-Authenticate header.
            responses: list[httpx.Response] = []
            try:
                r = httpx.get(
                    f"{url}/.well-known/oauth-protected-resource",
                    timeout=10,
                    verify=self._mcp_config.verify_ssl,
                    follow_redirects=False,
                )
                responses.append(r)
            except Exception:
                pass

            try:
                r2 = httpx.post(url, timeout=10, verify=self._mcp_config.verify_ssl, follow_redirects=False)
                responses.append(r2)
            except Exception:
                pass

            if not responses:
                return (False, f"MCP server {self.name} unreachable: no HTTP response from either endpoint")

            # Prefer the response with a WWW-Authenticate header (needed for PRM discovery)
            response = next(
                (r for r in responses if r.headers.get("www-authenticate")),
                responses[0],
            )

            # Auto-discover OAuth endpoints if not configured
            if not oauth_config.authorization_url or not oauth_config.token_url or not oauth_config.client_id:
                discovered = self._discover_oauth_endpoints(url, response)
                if not discovered:
                    return (False, f"MCP server {self.name}: OAuth enabled but auto-discovery failed. Configure authorization_url, token_url, and client_id manually.")

        except Exception as e:
            return (False, f"MCP server {self.name} unreachable: {_extract_root_error_message(e)}")

        # Register a placeholder tool that will trigger OAuth on first call.
        # After auth succeeds, _invoke will load the real tools dynamically.
        placeholder = MCP_Tool(
            name=self.connect_tool_name,
            description=f"Connect to {self.name} (requires OAuth authentication). Call this tool to authenticate and discover available tools.",
            inputSchema={"type": "object", "properties": {}},
        )
        self.tools = [RemoteMCPTool.create(placeholder, self)]
        logging.info("OAuth MCP server %s is reachable, registered placeholder tool (auth required)", self.name)
        return (True, "")

    def _discover_oauth_endpoints(self, mcp_url: str, initial_response: httpx.Response) -> bool:
        """Auto-discover OAuth endpoints following the MCP SDK's discovery flow.

        Discovery order (matching mcp.client.auth):
        1. Try Protected Resource Metadata (RFC 9728) — path-based, then root-based
        2. If PRM found auth server → fetch its OIDC/OAuth metadata
        3. If PRM not found → legacy fallback on MCP server itself
        4. Dynamic Client Registration deferred to runtime

        Returns True if discovery succeeded and oauth config is fully populated.
        """
        if not isinstance(self._mcp_config, MCPConfig) or self._mcp_config.oauth is None:
            return False
        oauth_config = self._mcp_config.oauth
        verify_ssl = self._mcp_config.verify_ssl

        # Step 1: Find auth server via Protected Resource Metadata (RFC 9728)
        auth_server_url, prm_scopes = discover_auth_server_from_prm(
            initial_response, mcp_url, verify_ssl, self.name,
        )
        if prm_scopes and not oauth_config.scopes:
            oauth_config.scopes = prm_scopes

        # Step 2: Fetch OAuth/OIDC metadata
        oidc_config = fetch_oauth_metadata(auth_server_url, mcp_url, verify_ssl, self.name)
        if not oidc_config:
            return False

        if not oauth_config.authorization_url:
            oauth_config.authorization_url = oidc_config.get("authorization_endpoint")
        if not oauth_config.token_url:
            oauth_config.token_url = oidc_config.get("token_endpoint")

        if not oauth_config.authorization_url or not oauth_config.token_url:
            logging.warning("OAuth discovery %s: missing authorization or token endpoint in metadata", self.name)
            return False

        if oidc_config.get("registration_endpoint"):
            oauth_config.registration_endpoint = oidc_config["registration_endpoint"]

        # DCR deferred to runtime — we don't know redirect_uri at startup
        if not oauth_config.client_id:
            if oauth_config.registration_endpoint:
                logging.debug("OAuth discovery %s: no client_id, DCR deferred to runtime", self.name)
            else:
                logging.warning("OAuth discovery %s: no client_id and no DCR endpoint", self.name)

        logging.debug(
            "OAuth discovery %s complete: authorization_url=%s, token_url=%s, client_id=%s",
            self.name, oauth_config.authorization_url, oauth_config.token_url, oauth_config.client_id,
        )
        return True

    async def _get_server_tools(self):
        async with get_initialized_mcp_session(self, None) as session:
            return await session.list_tools()

    async def _get_server_tools_with_context(self, request_context: Optional[Dict[str, Any]]):
        async with get_initialized_mcp_session(self, request_context) as session:
            return await session.list_tools()
