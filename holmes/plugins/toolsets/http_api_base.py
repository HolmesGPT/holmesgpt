import json
from abc import ABC
from typing import Any, Dict, Optional, Tuple, cast
from urllib.parse import urljoin

import requests
from pydantic import Field, model_validator
from requests.auth import HTTPBasicAuth

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    Toolset,
)
from holmes.utils.header_rendering import render_header_templates
from holmes.utils.pydantic_utils import ToolsetConfig


class HTTPAPIToolsetConfig(ToolsetConfig):
    api_url: str = Field(description="Base URL of the service API")
    bearer_token: Optional[str] = Field(
        default=None, description="Bearer token used for API authentication"
    )
    username: Optional[str] = Field(
        default=None, description="Username used for HTTP basic authentication"
    )
    password: Optional[str] = Field(
        default=None, description="Password used for HTTP basic authentication"
    )
    verify_ssl: bool = Field(default=True, description="Verify TLS certificates")
    timeout_seconds: int = Field(
        default=30, ge=1, le=300, description="HTTP request timeout in seconds"
    )
    extra_headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Additional HTTP headers; values support Holmes header templates",
    )

    @model_validator(mode="after")
    def validate_auth(self) -> "HTTPAPIToolsetConfig":
        if self.bearer_token and (self.username or self.password):
            raise ValueError(
                "authentication must use either bearer token or basic auth, not both"
            )
        if self.username and not self.password:
            raise ValueError("password is required when username is set")
        if self.password and not self.username:
            raise ValueError("username is required when password is set")
        return self


class HTTPAPIToolset(Toolset):
    @property
    def http_config(self) -> HTTPAPIToolsetConfig:
        return cast(HTTPAPIToolsetConfig, self.config)

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[str] = None,
        request_context: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        url = urljoin(self.http_config.api_url.rstrip("/") + "/", endpoint.lstrip("/"))
        request_headers = {"Accept": "application/json"}
        if self.http_config.bearer_token:
            request_headers["Authorization"] = f"Bearer {self.http_config.bearer_token}"
        if self.http_config.extra_headers:
            rendered_headers = render_header_templates(
                extra_headers=self.http_config.extra_headers,
                request_context=request_context,
                source_name=self.name,
            )
            if rendered_headers:
                request_headers.update(rendered_headers)
        if headers:
            request_headers.update(headers)

        auth = None
        if self.http_config.username and self.http_config.password:
            auth = HTTPBasicAuth(
                username=self.http_config.username,
                password=self.http_config.password,
            )

        response = requests.request(
            method,
            url,
            params=params,
            data=data,
            headers=request_headers,
            auth=auth,
            timeout=self.http_config.timeout_seconds,
            verify=self.http_config.verify_ssl,
        )
        response.raise_for_status()
        return response

    def _health_check(
        self,
        config_class: type[HTTPAPIToolsetConfig],
        config: Dict[str, Any],
        endpoint: str,
    ) -> Tuple[bool, str]:
        try:
            self.config = config_class(**config)
            self._request("GET", endpoint)
            return True, f"{self.name} API is accessible at {self.http_config.api_url}"
        except Exception as error:
            return False, f"Failed to connect to {self.name}: {error}"


class HTTPAPITool(Tool, ABC):
    def __init__(self, toolset: HTTPAPIToolset, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _get_json(
        self,
        endpoint: str,
        params: Dict[str, Any],
        context: ToolInvokeContext,
        *,
        query_params: Optional[Dict[str, Any]] = None,
    ) -> StructuredToolResult:
        try:
            response = self._toolset._request(
                "GET",
                endpoint,
                params=query_params,
                request_context=context.request_context,
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response.json(),
                params=params,
                url=response.url,
            )
        except requests.exceptions.HTTPError as error:
            status = (
                error.response.status_code
                if error.response is not None
                else "unknown"
            )
            body = error.response.text if error.response is not None else ""
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"{self._toolset.name} request to {endpoint} failed "
                    f"with HTTP {status}. "
                    f"Query parameters: {json.dumps(query_params or {}, default=str)}. "
                    f"Response body: {body}"
                ),
                params=params,
            )
        except requests.exceptions.RequestException as error:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Failed to reach {self._toolset.name} endpoint "
                    f"{endpoint}: {error}. "
                    f"Query parameters: {json.dumps(query_params or {}, default=str)}"
                ),
                params=params,
            )
