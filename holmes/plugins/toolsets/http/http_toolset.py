import fnmatch
import json
import logging
import os
import re
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple, Type
from urllib.parse import urlparse

import requests  # type: ignore
from pydantic import BaseModel, Field, model_validator

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
)
from holmes.plugins.toolsets.json_filter_mixin import JsonFilterMixin

logger = logging.getLogger(__name__)

ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


class AuthConfig(BaseModel):
    type: Literal["none", "basic", "bearer", "header"] = "none"
    # For basic auth
    username: Optional[str] = None
    password: Optional[str] = None
    # For bearer auth
    token: Optional[str] = None
    # For custom header auth
    name: Optional[str] = None
    value: Optional[str] = None

    @model_validator(mode="after")
    def validate_auth_fields(self) -> "AuthConfig":
        if self.type == "basic":
            if not self.username or not self.password:
                raise ValueError("Basic auth requires 'username' and 'password'")
        elif self.type == "bearer":
            if not self.token:
                raise ValueError("Bearer auth requires 'token'")
        elif self.type == "header":
            if not self.name or not self.value:
                raise ValueError("Header auth requires 'name' and 'value'")
        return self


class EndpointConfig(BaseModel):
    hosts: List[str] = Field(
        description="List of allowed host patterns (e.g., ['*.atlassian.net', 'confluence.mycompany.com'])",
        examples=[["api.example.com"]],
    )
    paths: List[str] = Field(
        default_factory=lambda: ["*"],
        description="Allowed path patterns (glob-style). Default allows all paths.",
        examples=[["/api/*", "/v2/*"]],
    )
    methods: List[str] = Field(
        default_factory=lambda: ["GET"],
        description="Allowed HTTP methods. Default is GET only.",
        examples=[["GET", "POST"]],
    )
    auth: AuthConfig = Field(
        default_factory=AuthConfig,
        description="Authentication configuration for this endpoint.",
    )
    health_check_url: Optional[str] = Field(
        default=None,
        description="Optional URL to verify auth at initialization time.",
        examples=["https://api.example.com/health"],
    )

    def get_methods(self) -> List[str]:
        return [m.upper() for m in self.methods]


class HttpToolsetConfig(BaseModel):
    endpoints: List[EndpointConfig] = Field(default_factory=list)
    verify_ssl: bool = True
    timeout_seconds: int = 30
    default_headers: Dict[str, str] = Field(default_factory=dict)


class HttpToolset(Toolset):
    """Generic HTTP toolset for making requests to whitelisted endpoints.

    Supports multiple instances via `type: http` in config.
    Each instance gets its own tool name, endpoints, and LLM instructions.
    """

    config_classes: ClassVar[List[Type[HttpToolsetConfig]]] = [HttpToolsetConfig]

    def __init__(self, name: str = "http", **kwargs: Any):
        llm_instructions = kwargs.pop("llm_instructions", None)
        config = kwargs.pop("config", None)
        enabled = kwargs.pop("enabled", False)
        kwargs.pop("type", None)

        description = kwargs.pop("description", None)
        if not description:
            if name == "http":
                description = "Generic HTTP client for making requests to whitelisted API endpoints"
            else:
                description = f"HTTP client for {name} API"

        super().__init__(
            name=name,
            description=description,
            icon_url="https://cdn-icons-png.flaticon.com/512/2165/2165004.png",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/http/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            enabled=enabled,
            **kwargs,
        )
        self._http_config: Optional[HttpToolsetConfig] = None

        if config:
            self.config = config

        self._user_llm_instructions = llm_instructions

    def _derive_tool_name(self) -> str:
        return self.name.replace("/", "_").replace("-", "_") + "_request"

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            self._http_config = HttpToolsetConfig(**config)
            self.config = self._http_config

            if not self._http_config.endpoints:
                return (
                    False,
                    "No endpoints configured. Add at least one endpoint with hosts and auth.",
                )

            for i, endpoint in enumerate(self._http_config.endpoints):
                if not endpoint.hosts:
                    return False, f"Endpoint {i} has no hosts configured."

                for method in endpoint.get_methods():
                    if method not in ALL_METHODS:
                        return False, f"Endpoint {i} has invalid method: {method}. Allowed: {ALL_METHODS}"

            # Perform health checks
            for i, endpoint in enumerate(self._http_config.endpoints):
                if endpoint.health_check_url:
                    success, error_msg = self._check_endpoint_health(endpoint, i)
                    if not success:
                        return False, error_msg

            tool_name = self._derive_tool_name()

            endpoints_summary = ", ".join(
                f"{ep.hosts[0] if len(ep.hosts) == 1 else f'{len(ep.hosts)} hosts'}"
                for ep in self._http_config.endpoints[:2]
            )
            if len(self._http_config.endpoints) > 2:
                endpoints_summary += f", +{len(self._http_config.endpoints) - 2} more"

            if self.name == "http":
                tool_description = f"Make HTTP requests to whitelisted API endpoints ({endpoints_summary})"
            else:
                tool_description = f"Make HTTP requests to {self.name} API ({endpoints_summary})"

            self.tools = [HttpRequest(self, tool_name=tool_name, tool_description=tool_description)]

            self._load_llm_instructions_from_file(
                os.path.dirname(__file__), "instructions.jinja2"
            )

            if self._user_llm_instructions:
                self.llm_instructions = (
                    (self.llm_instructions or "")
                    + "\n\n## API-Specific Instructions\n\n"
                    + self._user_llm_instructions
                )

            endpoint_count = len(self._http_config.endpoints)
            host_count = sum(len(ep.hosts) for ep in self._http_config.endpoints)
            return (
                True,
                f"HTTP toolset '{self.name}' configured with {endpoint_count} endpoint(s) covering {host_count} host pattern(s).",
            )

        except Exception as e:
            return False, f"Failed to validate HTTP configuration: {str(e)}"

    def _build_curl_command(self, endpoint: EndpointConfig, url: str) -> str:
        parts = ["curl", "-v"]

        auth = endpoint.auth
        if auth.type == "basic":
            parts.append('-u "$USERNAME:$PASSWORD"')
        elif auth.type == "bearer":
            parts.append('-H "Authorization: Bearer $TOKEN"')
        elif auth.type == "header" and auth.name:
            parts.append(f'-H "{auth.name}: $SECRET"')

        parts.append(f'"{url}"')
        return " ".join(parts)

    def _check_endpoint_health(
        self, endpoint: EndpointConfig, endpoint_index: int
    ) -> Tuple[bool, str]:
        url = endpoint.health_check_url
        if not url:
            return True, ""

        curl_cmd = self._build_curl_command(endpoint, url)

        try:
            headers = self.build_headers(endpoint)
            basic_auth = self.get_basic_auth(endpoint)

            response = requests.get(
                url,
                headers=headers,
                auth=basic_auth,
                timeout=10,
                verify=self._http_config.verify_ssl if self._http_config else True,
            )

            if response.ok:
                logger.info(f"Health check passed for endpoint {endpoint_index}: {url}")
                return True, ""
            else:
                return (
                    False,
                    f"Health check failed for endpoint {endpoint_index} ({url}): "
                    f"HTTP {response.status_code} - {response.text[:200]}\n"
                    f"To troubleshoot, run: {curl_cmd}",
                )

        except requests.exceptions.ConnectionError as e:
            return (
                False,
                f"Health check failed for endpoint {endpoint_index} ({url}): "
                f"Connection error - {e}\n"
                f"To troubleshoot, run: {curl_cmd}",
            )
        except requests.exceptions.Timeout:
            return (
                False,
                f"Health check failed for endpoint {endpoint_index} ({url}): "
                f"Request timed out\n"
                f"To troubleshoot, run: {curl_cmd}",
            )
        except Exception as e:
            return (
                False,
                f"Health check failed for endpoint {endpoint_index} ({url}): {e}\n"
                f"To troubleshoot, run: {curl_cmd}",
            )

    @property
    def http_config(self) -> HttpToolsetConfig:
        if self._http_config is None:
            raise RuntimeError(
                "HTTP toolset not configured. Call prerequisites_callable first."
            )
        return self._http_config

    def match_endpoint(
        self, url: str
    ) -> Tuple[Optional[EndpointConfig], Optional[str]]:
        try:
            parsed = urlparse(url)
        except Exception as e:
            return None, f"Invalid URL: {e}"

        if not parsed.scheme or not parsed.netloc:
            return None, f"Invalid URL format: {url}"

        host = parsed.hostname or parsed.netloc
        path = parsed.path or "/"

        for endpoint in self.http_config.endpoints:
            for host_pattern in endpoint.hosts:
                if self._match_host(host, host_pattern):
                    if self._match_path(path, endpoint.paths):
                        return endpoint, None

        return (
            None,
            f"URL not in whitelist. Host '{host}' with path '{path}' does not match any configured endpoint.",
        )

    def _match_host(self, host: str, pattern: str) -> bool:
        if pattern.startswith("*."):
            return host.lower().endswith(pattern[1:].lower())
        else:
            return host.lower() == pattern.lower()

    def _match_path(self, path: str, patterns: List[str]) -> bool:
        for pattern in patterns:
            if pattern == "*":
                return True
            if fnmatch.fnmatch(path, pattern):
                return True
        return False

    def is_method_allowed(self, method: str, endpoint: EndpointConfig) -> bool:
        return method.upper() in endpoint.get_methods()

    def build_headers(
        self, endpoint: EndpointConfig, extra_headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if self._http_config:
            headers.update(self._http_config.default_headers)

        auth = endpoint.auth
        if auth.type == "bearer":
            headers["Authorization"] = f"Bearer {auth.token}"
        elif auth.type == "header":
            if auth.name and auth.value:
                headers[auth.name] = auth.value

        if extra_headers:
            headers.update(extra_headers)

        return headers

    def get_basic_auth(self, endpoint: EndpointConfig) -> Optional[Tuple[str, str]]:
        if (
            endpoint.auth.type == "basic"
            and endpoint.auth.username
            and endpoint.auth.password
        ):
            return (endpoint.auth.username, endpoint.auth.password)
        return None


class HttpRequest(Tool, JsonFilterMixin):
    def __init__(self, toolset: HttpToolset, tool_name: str = "http_request", tool_description: Optional[str] = None):
        if not tool_description:
            if toolset.name == "http":
                tool_description = "Make HTTP requests to whitelisted API endpoints"
            else:
                tool_description = f"Make HTTP requests to {toolset.name} API endpoints"

        base_params = {
            "url": ToolParameter(
                description="The full URL to request (must match a whitelisted endpoint)",
                type="string",
                required=True,
            ),
            "method": ToolParameter(
                description="HTTP method (default: GET). Must be allowed by the endpoint configuration.",
                type="string",
                required=False,
            ),
            "body": ToolParameter(
                description="Request body (JSON string) for POST/PUT/PATCH requests",
                type="string",
                required=False,
            ),
            "headers": ToolParameter(
                description="Additional HTTP headers as a JSON object (optional, overrides defaults)",
                type="string",
                required=False,
            ),
        }

        parameters = JsonFilterMixin.extend_parameters(base_params)

        super().__init__(
            name=tool_name,
            description=tool_description,
            parameters=parameters,
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        url = params.get("url", "")
        method = params.get("method", "GET").upper()
        body = params.get("body")
        extra_headers_str = params.get("headers")

        endpoint, error = self._toolset.match_endpoint(url)
        if error or endpoint is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error or "URL not matched",
                params=params,
                url=url,
            )

        if method not in ALL_METHODS:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unsupported HTTP method: {method}. Supported: {ALL_METHODS}",
                params=params,
                url=url,
            )

        if not self._toolset.is_method_allowed(method, endpoint):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Method {method} not allowed for this endpoint. Allowed methods: {endpoint.get_methods()}",
                params=params,
                url=url,
            )

        extra_headers = None
        if extra_headers_str:
            try:
                extra_headers = json.loads(extra_headers_str)
                if not isinstance(extra_headers, dict):
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error="Headers must be a JSON object, not a list or primitive",
                        params=params,
                        url=url,
                    )
            except json.JSONDecodeError as e:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Invalid headers JSON: {e}",
                    params=params,
                    url=url,
                )

        headers = self._toolset.build_headers(endpoint, extra_headers)
        basic_auth = self._toolset.get_basic_auth(endpoint)
        timeout = self._toolset.http_config.timeout_seconds
        verify_ssl = self._toolset.http_config.verify_ssl

        try:
            request_kwargs: Dict[str, Any] = {
                "headers": headers,
                "auth": basic_auth,
                "timeout": timeout,
                "verify": verify_ssl,
            }

            if method in ("POST", "PUT", "PATCH") and body:
                request_kwargs["data"] = body

            response = requests.request(method, url, **request_kwargs)

            try:
                data = response.json()
            except Exception:
                data = response.text

            if response.ok:
                result = StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data={"status_code": response.status_code, "body": data},
                    params=params,
                    url=url,
                )
            else:
                result = StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"HTTP {response.status_code}: {data}",
                    data={"status_code": response.status_code, "body": data},
                    params=params,
                    url=url,
                )

            return self.filter_result(result, params)

        except requests.exceptions.Timeout:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Request timed out after {timeout}s",
                params=params,
                url=url,
            )
        except requests.exceptions.ConnectionError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Connection error: {e}",
                params=params,
                url=url,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Request failed: {e}",
                params=params,
                url=url,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        url = params.get("url", "unknown")
        method = params.get("method", "GET").upper()
        if len(url) > 50:
            url = url[:47] + "..."
        return f"HTTP {method} {url}"
