import json
import logging
from functools import partial
from typing import Any, Callable, ClassVar, Dict, Optional, Tuple, Type

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import AnyUrl, Field, model_validator

from holmes.common.env_vars import SSE_READ_TIMEOUT
from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig, MCPMode, RemoteMCPToolset

AWS_MCP_SERVICE = "aws-mcp"
AWS_MCP_URL_TEMPLATE = "https://aws-mcp.{region}.api.aws/mcp"


class AwsMCPConfig(MCPConfig):
    _name: ClassVar[Optional[str]] = "AWS MCP Server"
    _description: ClassVar[Optional[str]] = (
        "Hosted AWS MCP Server, requests are SigV4-signed with your AWS credentials"
    )

    mode: MCPMode = Field(default=MCPMode.AWS, title="Mode")
    region: str = Field(
        title="Region",
        description="AWS region: selects the regional AWS MCP endpoint and is the default region for AWS API calls.",
        examples=["us-east-1"],
    )
    profile: Optional[str] = Field(
        default=None,
        title="AWS profile",
        description="boto3 profile to sign with. Omit to use the default credential chain (env vars, IRSA, ~/.aws).",
        examples=["prod"],
    )
    url: AnyUrl = Field(
        default=None,  # type: ignore[assignment]
        title="URL",
        description="AWS MCP Server endpoint. Defaults to the regional endpoint for `region`.",
        examples=["https://aws-mcp.us-east-1.api.aws/mcp"],
    )

    @model_validator(mode="before")
    @classmethod
    def default_url(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(values, dict) and not values.get("url") and values.get("region"):
            values["url"] = AWS_MCP_URL_TEMPLATE.format(region=values["region"])
        return values

    def get_lock_string(self) -> str:
        return f"{self.url}#{self.profile or ''}"


def aws_session(profile: Optional[str]) -> boto3.Session:
    return boto3.Session(profile_name=profile)


async def _inject_region_hook(region: str, request: httpx.Request) -> None:
    if not request.content:
        return
    body = json.loads(request.content)
    if not isinstance(body, dict) or "jsonrpc" not in body:
        return
    params = body.setdefault("params", {})
    params["_meta"] = {"AWS_REGION": region, **params.get("_meta", {})}
    content = json.dumps(body).encode()
    request.stream = httpx.ByteStream(content)
    request._content = content
    request.headers["content-length"] = str(len(content))


async def _sign_request_hook(
    region: str, profile: Optional[str], request: httpx.Request
) -> None:
    credentials = aws_session(profile).get_credentials()
    if credentials is None:
        raise ValueError(
            f"No AWS credentials found for profile '{profile or 'default'}'"
        )
    headers = dict(request.headers)
    headers.pop("connection", None)
    aws_request = AWSRequest(
        method=request.method,
        url=str(request.url),
        data=request.content,
        headers=headers,
    )
    SigV4Auth(credentials, AWS_MCP_SERVICE, region).add_auth(aws_request)
    request.headers.update(dict(aws_request.headers))


def create_sigv4_http_client(
    config: AwsMCPConfig,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[httpx.Timeout] = None,
    auth: Optional[httpx.Auth] = None,
) -> httpx.AsyncClient:
    hooks: list[Callable[..., Any]] = [
        partial(_inject_region_hook, config.region),
        partial(_sign_request_hook, config.region, config.profile),
    ]
    return httpx.AsyncClient(
        follow_redirects=False,
        verify=config.verify_ssl,
        headers=headers,
        timeout=timeout or httpx.Timeout(SSE_READ_TIMEOUT),
        event_hooks={"request": hooks},
    )


class AwsMCPToolset(RemoteMCPToolset):
    config_classes: ClassVar[list[Type[Any]]] = [AwsMCPConfig]
    description: str = "AWS MCP Server toolset"

    def _build_mcp_config(self, config: dict) -> AwsMCPConfig:
        return AwsMCPConfig(**config)

    def _http_client_factory(self):
        if self.is_oauth_enabled:
            return super()._http_client_factory()
        return partial(create_sigv4_http_client, self._mcp_config)

    def _check_aws_credentials(self) -> Tuple[bool, str]:
        config: AwsMCPConfig = self._mcp_config  # type: ignore[assignment]
        try:
            identity = (
                aws_session(config.profile)
                .client("sts", region_name=config.region)
                .get_caller_identity()
            )
        except (BotoCoreError, ClientError, OSError) as e:
            return (
                False,
                f"AWS credentials check failed for {self.name} (profile '{config.profile or 'default'}'): {e}",
            )
        logging.info("MCP server %s authenticated as %s", self.name, identity["Arn"])
        return (True, "")

    def prerequisites_callable(self, config) -> Tuple[bool, str]:
        if not config:
            return (False, f"Config is required for {self.name}")
        self._mcp_config = self._build_mcp_config(config)
        if not self.is_oauth_enabled:
            ok, message = self._check_aws_credentials()
            if not ok:
                return (ok, message)
        return super().prerequisites_callable(config)
