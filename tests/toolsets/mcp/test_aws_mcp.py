"""Tests for the hosted AWS MCP Server toolset (SigV4-signed streamable-http)."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from botocore.exceptions import ClientError

from holmes.core.tools import ToolsetType
from holmes.plugins.toolsets import load_toolsets_from_config
from holmes.plugins.toolsets.mcp.aws_mcp import (
    AwsMCPConfig,
    AwsMCPToolset,
    create_sigv4_http_client,
)
from holmes.plugins.toolsets.mcp.toolset_mcp import (
    MCPMode,
    RemoteMCPTool,
    RemoteMCPToolset,
)

ENDPOINT = "https://aws-mcp.us-east-1.api.aws/mcp"
FAKE_CREDENTIALS = Credentials("AKIAEXAMPLE", "secret", "session-token")
TOOL_CALL = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "aws___list_regions", "arguments": {}},
}


def _mcp_servers(config: dict) -> dict:
    return {
        "aws": {"type": ToolsetType.MCP.value, "description": "aws", "config": config}
    }


def test_config_defaults_url_from_region():
    config = AwsMCPConfig(region="eu-west-1")
    assert str(config.url) == "https://aws-mcp.eu-west-1.api.aws/mcp"
    assert config.mode == MCPMode.AWS


def test_config_keeps_explicit_url():
    config = AwsMCPConfig(region="eu-west-1", url="https://example.com/mcp")
    assert str(config.url) == "https://example.com/mcp"


def test_lock_string_differs_per_profile():
    assert (
        AwsMCPConfig(region="us-east-1", profile="dev").get_lock_string()
        != AwsMCPConfig(region="us-east-1", profile="prod").get_lock_string()
    )


def test_mode_aws_dispatches_to_aws_toolset():
    toolset = load_toolsets_from_config(
        _mcp_servers({"mode": "aws", "region": "us-east-1"}), strict_check=False
    )[0]
    assert isinstance(toolset, AwsMCPToolset)
    assert toolset.type == ToolsetType.MCP


def test_streamable_http_mode_keeps_plain_toolset():
    toolset = load_toolsets_from_config(
        _mcp_servers({"mode": "streamable-http", "url": ENDPOINT}), strict_check=False
    )[0]
    assert type(toolset) is RemoteMCPToolset


@respx.mock
async def test_requests_are_signed_after_region_metadata_injection():
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={}))
    config = AwsMCPConfig(region="us-east-1", profile="prod")
    with patch("holmes.plugins.toolsets.mcp.aws_mcp.aws_session") as session:
        session.return_value.get_credentials.return_value = FAKE_CREDENTIALS
        async with create_sigv4_http_client(config) as client:
            await client.post(ENDPOINT, json=TOOL_CALL)
    session.assert_called_with("prod")
    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["params"]["_meta"] == {"AWS_REGION": "us-east-1"}
    assert request.headers["x-amz-security-token"] == "session-token"
    assert "Credential=AKIAEXAMPLE/" in request.headers["authorization"]
    assert "/us-east-1/aws-mcp/aws4_request" in request.headers["authorization"]
    expected = AWSRequest(
        method="POST",
        url=ENDPOINT,
        data=request.content,
        headers={
            k: v
            for k, v in request.headers.items()
            if k not in ("authorization", "connection")
        },
    )
    SigV4Auth(FAKE_CREDENTIALS, "aws-mcp", "us-east-1").add_auth(expected)
    assert (
        expected.headers["Authorization"].split("Signature=")[1]
        == request.headers["authorization"].split("Signature=")[1]
    )


async def test_missing_credentials_raise():
    with patch("holmes.plugins.toolsets.mcp.aws_mcp.aws_session") as session:
        session.return_value.get_credentials.return_value = None
        async with create_sigv4_http_client(AwsMCPConfig(region="us-east-1")) as client:
            with pytest.raises(ValueError, match="No AWS credentials"):
                await client.post(ENDPOINT, json=TOOL_CALL)


def test_prerequisites_report_sts_failure():
    toolset = AwsMCPToolset(
        name="aws", description="aws", config={"mode": "aws", "region": "us-east-1"}
    )
    error = ClientError(
        {"Error": {"Code": "InvalidClientTokenId", "Message": "bad token"}},
        "GetCallerIdentity",
    )
    with patch("holmes.plugins.toolsets.mcp.aws_mcp.aws_session") as session:
        session.return_value.client.return_value.get_caller_identity.side_effect = error
        ok, message = toolset.prerequisites_callable(toolset.config)
    assert not ok
    assert "InvalidClientTokenId" in message


def test_prerequisites_load_tools_after_sts_success():
    toolset = AwsMCPToolset(
        name="aws", description="aws", config={"mode": "aws", "region": "us-east-1"}
    )
    with patch(
        "holmes.plugins.toolsets.mcp.aws_mcp.aws_session"
    ) as session, patch.object(
        AwsMCPToolset, "_load_remote_tools", return_value=[]
    ) as load:
        session.return_value.client.return_value.get_caller_identity.return_value = {
            "Arn": "arn:aws:iam::1:role/holmes"
        }
        ok, _ = toolset.prerequisites_callable(toolset.config)
    assert ok
    load.assert_called_once()


def test_run_script_one_liner_shows_first_line():
    tool = RemoteMCPTool(
        name="aws___run_script",
        mcp_tool_name="aws___run_script",
        description="d",
        parameters={},
        toolset=MagicMock(spec=RemoteMCPToolset),
    )
    assert (
        tool._base_one_liner(
            {"code": "buckets = call_boto3('s3', 'ListBuckets')\nprint(buckets)"}
        )
        == "aws run_script: buckets = call_boto3('s3', 'ListBuckets')"
    )
