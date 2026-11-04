import json
import logging
from functools import lru_cache, partial
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import AnyUrl, BaseModel, Field, model_validator

from holmes.common.env_vars import SSE_READ_TIMEOUT
from holmes.core.tools import ToolParameter
from holmes.plugins.toolsets.mcp.toolset_mcp import (
    MCPConfig,
    MCPMode,
    RemoteMCPTool,
    RemoteMCPToolset,
)

AWS_MCP_SERVICE = "aws-mcp"
AWS_MCP_URL_TEMPLATE = "https://aws-mcp.{region}.api.aws/mcp"


class AwsAccountConfig(BaseModel):
    account_id: Optional[str] = None
    region: Optional[str] = None
    description: Optional[str] = None


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
    accounts: Dict[str, AwsAccountConfig] = Field(
        default_factory=dict,
        title="Accounts",
        description="AWS profiles this server can use; every tool call picks one by name.",
    )

    @model_validator(mode="before")
    @classmethod
    def default_url(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(values, dict) and not values.get("url") and values.get("region"):
            values["url"] = AWS_MCP_URL_TEMPLATE.format(region=values["region"])
        return values

    def get_lock_string(self) -> str:
        return f"{self.url}#{self.profile or ''}"


@lru_cache(maxsize=None)
def aws_session(profile: Optional[str]) -> boto3.Session:
    return boto3.Session(profile_name=profile)


def _pick_account(config: AwsMCPConfig, body: dict) -> Tuple[Optional[str], str]:
    if not config.accounts:
        return config.profile, config.region
    name = next(iter(config.accounts))
    if body.get("method") == "tools/call":
        name = body.get("params", {}).get("arguments", {}).pop("account", None)
    if name not in config.accounts:
        raise ValueError(
            f"Unknown AWS account '{name}', use one of: {', '.join(config.accounts)}"
        )
    return name, config.accounts[name].region or config.region


def _set_region_meta(body: dict, region: str, request: httpx.Request) -> None:
    params = body.setdefault("params", {})
    params["_meta"] = {"AWS_REGION": region, **params.get("_meta", {})}
    content = json.dumps(body).encode()
    request.stream = httpx.ByteStream(content)
    request._content = content
    request.headers["content-length"] = str(len(content))


async def _sign_request_hook(config: AwsMCPConfig, request: httpx.Request) -> None:
    body = json.loads(request.content) if request.content else {}
    is_jsonrpc = isinstance(body, dict) and "jsonrpc" in body
    profile, region = _pick_account(config, body if is_jsonrpc else {})
    if is_jsonrpc:
        _set_region_meta(body, region, request)
    logging.debug("Signing AWS MCP request with profile %s for %s", profile, region)
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
    SigV4Auth(credentials, AWS_MCP_SERVICE, config.region).add_auth(aws_request)
    request.headers.update(dict(aws_request.headers))


def create_sigv4_http_client(
    config: AwsMCPConfig,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[httpx.Timeout] = None,
    auth: Optional[httpx.Auth] = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        verify=config.verify_ssl,
        headers=headers,
        timeout=timeout or httpx.Timeout(SSE_READ_TIMEOUT),
        event_hooks={"request": [partial(_sign_request_hook, config)]},
    )


class AwsMCPToolset(RemoteMCPToolset):
    config_classes: ClassVar[list[Type[Any]]] = [AwsMCPConfig]
    description: str = "AWS MCP Server toolset"

    def _build_mcp_config(self, config: dict) -> AwsMCPConfig:
        return AwsMCPConfig(**config)

    def _http_client_factory(self):
        return partial(create_sigv4_http_client, self._mcp_config)

    def _load_remote_tools(
        self, request_context: Optional[Dict[str, Any]] = None
    ) -> List[RemoteMCPTool]:
        tools = super()._load_remote_tools(request_context)
        accounts = self._mcp_config.accounts  # type: ignore[union-attr]
        if not accounts:
            return tools
        account_param = ToolParameter(
            description="AWS account to run against: "
            + "; ".join(
                f"{name} ({account.account_id or 'unknown id'}): {account.description or ''}"
                for name, account in accounts.items()
            ),
            enum=list(accounts),
        )
        for tool in tools:
            tool.parameters["account"] = account_param
        return tools

    def _check_aws_credentials(self) -> Tuple[bool, str]:
        config: AwsMCPConfig = self._mcp_config  # type: ignore[assignment]
        errors = []
        for profile in list(config.accounts) or [config.profile]:
            try:
                identity = (
                    aws_session(profile)
                    .client("sts", region_name=config.region)
                    .get_caller_identity()
                )
            except (BotoCoreError, ClientError, OSError) as e:
                errors.append(f"profile '{profile or 'default'}': {e}")
                config.accounts.pop(profile, None)  # type: ignore[arg-type]
                continue
            logging.info("MCP server %s authenticated as %s", self.name, identity["Arn"])
        if errors and not config.accounts:
            return (False, f"AWS credentials check failed for {self.name}: {'; '.join(errors)}")
        if errors:
            logging.warning("MCP server %s skipped AWS accounts: %s", self.name, "; ".join(errors))
        return (True, "")

    def prerequisites_callable(self, config) -> Tuple[bool, str]:
        if not config:
            return (False, f"Config is required for {self.name}")
        try:
            self._mcp_config = self._build_mcp_config(config)
        except Exception as e:
            return (False, f"Invalid AWS MCP config for {self.name}: {e}")
        ok, message = self._check_aws_credentials()
        if not ok:
            return (ok, message)
        working_accounts = self._mcp_config.model_dump()["accounts"]  # type: ignore[union-attr]
        return super().prerequisites_callable({**config, "accounts": working_accounts})
