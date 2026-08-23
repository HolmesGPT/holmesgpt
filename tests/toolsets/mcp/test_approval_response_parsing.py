"""Test MCP tool response parsing for remote approval requirements."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPTool, RemoteMCPToolset


@pytest.mark.asyncio
async def test_mcp_tool_parses_approval_required_response():
    """Verify that MCP tools correctly parse APPROVAL_REQUIRED responses from RemoteToolsProvider."""

    # Create a mock toolset
    mock_toolset = MagicMock(spec=RemoteMCPToolset)
    mock_toolset.name = "test_toolset"

    # Create a tool instance
    tool = RemoteMCPTool(
        name="test_tool",
        mcp_tool_name="test_tool",
        description="Test tool",
        parameters={},
        toolset=mock_toolset,
    )

    # Create a mock MCP session and tool result. The status is the serialized
    # StructuredToolResultStatus.APPROVAL_REQUIRED value — lowercase
    # "approval_required" — exactly as the executor/relay emit it over the wire.
    # (Using the uppercase "APPROVAL_REQUIRED" literal here previously enshrined
    # the parser bug that silently never matched, so the UI never prompted.)
    approval_response = {
        "agent_name": "prod-cluster",
        "status": StructuredToolResultStatus.APPROVAL_REQUIRED.value,
        "error": "Tool requires user approval",
        "data": None,
    }
    response_json = json.dumps(approval_response)

    mock_content_block = MagicMock()
    mock_content_block.type = "text"
    mock_content_block.text = response_json

    mock_tool_result = MagicMock()
    mock_tool_result.content = [mock_content_block]
    mock_tool_result.isError = False

    # Mock the MCP session
    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=mock_tool_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    # Patch get_initialized_mcp_session to use our mock
    with patch(
        "holmes.plugins.toolsets.mcp.toolset_mcp.get_initialized_mcp_session"
    ) as mock_get_session:
        mock_get_session.return_value = mock_session

        result = await tool._invoke_async(
            params={"test_param": "value"},
            request_context=None,
        )

    # Verify the result is APPROVAL_REQUIRED
    assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED
    assert result.error == "Tool requires user approval"
    assert result.params == {"test_param": "value"}


@pytest.mark.asyncio
async def test_mcp_tool_parses_normal_success_response():
    """Verify that normal SUCCESS responses still work correctly."""

    mock_toolset = MagicMock(spec=RemoteMCPToolset)
    mock_toolset.name = "test_toolset"

    tool = RemoteMCPTool(
        name="test_tool",
        mcp_tool_name="test_tool",
        description="Test tool",
        parameters={},
        toolset=mock_toolset,
    )

    # Create a normal tool response
    response_data = "Tool execution succeeded"

    mock_content_block = MagicMock()
    mock_content_block.type = "text"
    mock_content_block.text = response_data

    mock_tool_result = MagicMock()
    mock_tool_result.content = [mock_content_block]
    mock_tool_result.isError = False

    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=mock_tool_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "holmes.plugins.toolsets.mcp.toolset_mcp.get_initialized_mcp_session"
    ) as mock_get_session:
        mock_get_session.return_value = mock_session

        result = await tool._invoke_async(
            params={"test_param": "value"},
            request_context=None,
        )

    # Verify the result is SUCCESS
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == response_data


@pytest.mark.asyncio
async def test_mcp_tool_handles_malformed_json_gracefully():
    """Verify that malformed JSON responses don't crash and fall back to SUCCESS."""

    mock_toolset = MagicMock(spec=RemoteMCPToolset)
    mock_toolset.name = "test_toolset"

    tool = RemoteMCPTool(
        name="test_tool",
        mcp_tool_name="test_tool",
        description="Test tool",
        parameters={},
        toolset=mock_toolset,
    )

    # Create a response that looks like JSON but isn't valid
    response_data = "{invalid json}"

    mock_content_block = MagicMock()
    mock_content_block.type = "text"
    mock_content_block.text = response_data

    mock_tool_result = MagicMock()
    mock_tool_result.content = [mock_content_block]
    mock_tool_result.isError = False

    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=mock_tool_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "holmes.plugins.toolsets.mcp.toolset_mcp.get_initialized_mcp_session"
    ) as mock_get_session:
        mock_get_session.return_value = mock_session

        result = await tool._invoke_async(
            params={"test_param": "value"},
            request_context=None,
        )

    # Should fall back to treating it as normal text response
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == response_data


@pytest.mark.asyncio
async def test_invoke_forwards_user_approved_as_reserved_arg():
    """On an approved re-invocation the caller must inject the reserved approval
    arg into the outgoing MCP call so relay can forward it to the target; a
    normal (unapproved) call must NOT include it."""
    from holmes.plugins.toolsets.mcp.toolset_mcp import REMOTE_TOOL_APPROVED_PARAM

    tool = RemoteMCPTool(
        name="remote_bash",
        mcp_tool_name="bash",
        description="Remote bash",
        parameters={},
        toolset=MagicMock(spec=RemoteMCPToolset),
        is_remote=True,
    )

    ok = {"status": "success", "data": "ok"}
    block = MagicMock(type="text", text=json.dumps(ok))
    result_obj = MagicMock(content=[block], isError=False)

    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=result_obj)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "holmes.plugins.toolsets.mcp.toolset_mcp.get_initialized_mcp_session"
    ) as get_session:
        get_session.return_value = session

        # approved -> reserved arg injected
        await tool._invoke_async(
            params={"command": "curl x"}, request_context=None, user_approved=True
        )
        approved_args = session.call_tool.call_args.args[1]
        assert approved_args.get(REMOTE_TOOL_APPROVED_PARAM) is True
        assert approved_args["command"] == "curl x"

        # not approved -> reserved arg absent (generic MCP servers never see it)
        session.call_tool.reset_mock()
        await tool._invoke_async(
            params={"command": "curl x"}, request_context=None, user_approved=False
        )
        assert REMOTE_TOOL_APPROVED_PARAM not in session.call_tool.call_args.args[1]


@pytest.mark.asyncio
async def test_invoke_never_sends_reserved_arg_to_local_mcp_server():
    """Only relay pops the reserved approval arg. A local (non-remote) MCP
    server gets the call verbatim, so an approved re-invocation must NOT
    inject it — servers with strict signatures (e.g. kubernetes-remediation's
    run_kubectl_command) reject unexpected keyword arguments."""
    from holmes.plugins.toolsets.mcp.toolset_mcp import REMOTE_TOOL_APPROVED_PARAM

    tool = RemoteMCPTool(
        name="run_kubectl_command",
        mcp_tool_name="run_kubectl_command",
        description="Gated kubectl",
        parameters={},
        toolset=MagicMock(spec=RemoteMCPToolset),
        is_remote=False,
    )

    ok = {"status": "success", "data": "ok"}
    block = MagicMock(type="text", text=json.dumps(ok))
    result_obj = MagicMock(content=[block], isError=False)

    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=result_obj)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "holmes.plugins.toolsets.mcp.toolset_mcp.get_initialized_mcp_session"
    ) as get_session:
        get_session.return_value = session

        await tool._invoke_async(
            params={"args": ["scale", "deploy/x", "--replicas=2"]},
            request_context=None,
            user_approved=True,
        )
        sent_args = session.call_tool.call_args.args[1]
        assert REMOTE_TOOL_APPROVED_PARAM not in sent_args
        assert sent_args == {"args": ["scale", "deploy/x", "--replicas=2"]}


def test_remote_one_liner_names_target_cluster():
    """The approval description surfaced to the UI must name the target cluster
    for remote tools (mirrors the Slack prompt), and must hide the routing/
    reserved params so the user sees just the command + cluster."""
    from holmes.plugins.toolsets.mcp.toolset_mcp import (
        REMOTE_TOOL_APPROVED_PARAM,
        REMOTE_TOOL_SESSION_PREFIXES_PARAM,
    )

    tool = RemoteMCPTool(
        name="remote_bash",
        mcp_tool_name="bash",
        description="Remote bash",
        parameters={},
        toolset=MagicMock(spec=RemoteMCPToolset),
        is_remote=True,
    )

    one_liner = tool.get_parameterized_one_liner(
        {
            "cli_command": "curl http://svc",
            "agent_name": "eu-eks-prod-2",
            "instance": "abc",
            REMOTE_TOOL_APPROVED_PARAM: True,
            REMOTE_TOOL_SESSION_PREFIXES_PARAM: ["curl"],
        }
    )

    assert one_liner == "curl http://svc on remote cluster `eu-eks-prod-2`"


def test_remote_one_liner_without_agent_falls_back():
    """A remote tool call missing agent_name still gets a generic remote suffix."""
    tool = RemoteMCPTool(
        name="remote_bash",
        mcp_tool_name="bash",
        description="Remote bash",
        parameters={},
        toolset=MagicMock(spec=RemoteMCPToolset),
        is_remote=True,
    )

    one_liner = tool.get_parameterized_one_liner({"cli_command": "curl http://svc"})
    assert one_liner == "curl http://svc on a remote cluster"


def test_local_one_liner_has_no_remote_suffix():
    """Non-remote tools keep their plain one-liner (no cluster suffix)."""
    mock_toolset = MagicMock(spec=RemoteMCPToolset)
    mock_toolset.name = "test_toolset"
    tool = RemoteMCPTool(
        name="test_tool",
        mcp_tool_name="test_tool",
        description="Test tool",
        parameters={},
        toolset=mock_toolset,
    )

    one_liner = tool.get_parameterized_one_liner({"cli_command": "curl http://svc"})
    assert one_liner == "curl http://svc"
    assert "remote cluster" not in one_liner


def test_is_remote_field_governs_one_liner_not_name():
    """Remoteness is driven by the explicit `is_remote` field, never by sniffing
    the tool name. A tool whose name starts with 'remote_' but is not flagged
    remote gets no cluster suffix, and a tool flagged remote gets one regardless
    of its name."""
    named_remote_but_not = RemoteMCPTool(
        name="remote_bash",
        mcp_tool_name="bash",
        description="Remote bash",
        parameters={},
        toolset=MagicMock(spec=RemoteMCPToolset),
        is_remote=False,
    )
    assert (
        named_remote_but_not.get_parameterized_one_liner(
            {"cli_command": "curl http://svc", "agent_name": "eu-eks-prod-2"}
        )
        == "curl http://svc"
    )

    flagged_remote = RemoteMCPTool(
        name="plain_tool",
        mcp_tool_name="bash",
        description="Remote bash",
        parameters={},
        toolset=MagicMock(spec=RemoteMCPToolset),
        is_remote=True,
    )
    assert (
        flagged_remote.get_parameterized_one_liner(
            {"cli_command": "curl http://svc", "agent_name": "eu-eks-prod-2"}
        )
        == "curl http://svc on remote cluster `eu-eks-prod-2`"
    )


@pytest.mark.asyncio
async def test_is_remote_field_gates_session_prefix_injection():
    """`__robusta_session_approved_prefixes` is injected into the remote call only
    when the tool is flagged remote — the field, not the name, decides."""
    from holmes.plugins.toolsets.mcp.toolset_mcp import (
        REMOTE_TOOL_SESSION_PREFIXES_PARAM,
    )

    def _capturing_session():
        block = MagicMock(type="text", text="ok")
        result_obj = MagicMock(content=[block], isError=False)
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=result_obj)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        return session

    remote_tool = RemoteMCPTool(
        name="remote_bash",
        mcp_tool_name="bash",
        description="Remote bash",
        parameters={},
        toolset=MagicMock(spec=RemoteMCPToolset),
        is_remote=True,
    )
    local_tool = RemoteMCPTool(
        name="remote_bash",
        mcp_tool_name="bash",
        description="Remote bash",
        parameters={},
        toolset=MagicMock(spec=RemoteMCPToolset),
        is_remote=False,
    )

    remote_session = _capturing_session()
    with patch(
        "holmes.plugins.toolsets.mcp.toolset_mcp.get_initialized_mcp_session"
    ) as get_session:
        get_session.return_value = remote_session
        await remote_tool._invoke_async(
            params={"command": "curl http://svc"},
            request_context=None,
            session_approved_prefixes=["curl"],
        )
    remote_call_params = remote_session.call_tool.call_args.args[1]
    assert remote_call_params.get(REMOTE_TOOL_SESSION_PREFIXES_PARAM) == ["curl"]

    local_session = _capturing_session()
    with patch(
        "holmes.plugins.toolsets.mcp.toolset_mcp.get_initialized_mcp_session"
    ) as get_session:
        get_session.return_value = local_session
        await local_tool._invoke_async(
            params={"command": "curl http://svc"},
            request_context=None,
            session_approved_prefixes=["curl"],
        )
    local_call_params = local_session.call_tool.call_args.args[1]
    assert REMOTE_TOOL_SESSION_PREFIXES_PARAM not in local_call_params
