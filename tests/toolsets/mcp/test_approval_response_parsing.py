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
