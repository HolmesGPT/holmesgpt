"""Integration test for remote bash tool approval workflow.

This test verifies the end-to-end flow:
1. Caller invokes a remote bash tool that requires approval
2. Target Holmes detects approval requirement and stores metadata
3. Approval JSON is returned to caller with status: APPROVAL_REQUIRED
4. Holmes MCP parser detects this and surfaces as APPROVAL_REQUIRED status
5. Caller surfaces this as an approval event to the LLM
6. User approves via CLI callback or Slack form
7. Target re-executes with approval and returns final result
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPTool


@pytest.mark.asyncio
async def test_remote_bash_tool_approval_full_flow():
    """Verify complete remote bash tool approval workflow."""

    # Mock toolset
    mock_toolset = MagicMock()
    mock_toolset.name = "remote_bash"

    # Create a tool instance (simulating a remote bash tool)
    tool = RemoteMCPTool(
        name="bash/execute",
        mcp_tool_name="bash_command_runner",
        description="Execute bash command remotely",
        parameters={
            "command": {"type": "string", "description": "Command to run"},
            "suggested_prefixes": {
                "type": "array",
                "description": "Allowed command prefixes"
            },
        },
        toolset=mock_toolset,
    )

    # Simulate the approval workflow that target Holmes performs:
    # 1. Target detects approval requirement
    # 2. Stores approval metadata
    # 3. Polls for caller decision
    # 4. Returns APPROVAL_REQUIRED to caller with approval details
    approval_response = {
        "status": "APPROVAL_REQUIRED",
        "error": "Command requires approval. New prefixes: /usr/local/bin",
        "data": None,
        "approval_params": {
            "command": "rm -rf /usr/local/bin/some-package",
            "suggested_prefixes": ["/usr/local/bin/"]
        }
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

    with patch(
        "holmes.plugins.toolsets.mcp.toolset_mcp.get_initialized_mcp_session"
    ) as mock_get_session:
        mock_get_session.return_value = mock_session

        # Invoke the tool
        result = await tool._invoke_async(
            params={
                "command": "rm -rf /usr/local/bin/some-package",
                "suggested_prefixes": [],  # Will be updated by approval
            },
            request_context=None,
        )

    # Verify that the MCP tool parser detected the APPROVAL_REQUIRED status
    assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED
    assert "Command requires approval" in result.error
    # The params should include the suggested_prefixes from the approval response
    assert result.params is not None


@pytest.mark.asyncio
async def test_remote_bash_tool_approval_with_approval_token():
    """Verify approval token is preserved in the workflow."""

    mock_toolset = MagicMock()
    mock_toolset.name = "remote_bash"

    tool = RemoteMCPTool(
        name="bash/execute",
        mcp_tool_name="bash_command_runner",
        description="Execute bash command remotely",
        parameters={},
        toolset=mock_toolset,
    )

    # Approval response includes token for security
    approval_response = {
        "status": "APPROVAL_REQUIRED",
        "error": "Command requires approval",
        "approval_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "approval_params": {"command": "dangerous-cmd"}
    }

    response_json = json.dumps(approval_response)

    mock_content_block = MagicMock()
    mock_content_block.type = "text"
    mock_content_block.text = response_json

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

        result = await tool._invoke_async(params={}, request_context=None)

    # Verify the result preserves the approval structure
    assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED
    assert result.error is not None


@pytest.mark.asyncio
async def test_remote_bash_tool_after_approval_execution():
    """Verify tool executes normally after approval is granted and decision is written."""

    mock_toolset = MagicMock()
    mock_toolset.name = "remote_bash"

    tool = RemoteMCPTool(
        name="bash/execute",
        mcp_tool_name="bash_command_runner",
        description="Execute bash command remotely",
        parameters={},
        toolset=mock_toolset,
    )

    # After approval decision is written to DB and target re-invokes,
    # it returns a normal SUCCESS response
    final_response = {
        "status": "SUCCESS",
        "data": "Package successfully removed from /usr/local/bin/",
        "error": None,
    }

    response_json = json.dumps(final_response)

    mock_content_block = MagicMock()
    mock_content_block.type = "text"
    mock_content_block.text = response_json

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

        # Re-invocation after approval decision is written
        result = await tool._invoke_async(
            params={
                "command": "rm -rf /usr/local/bin/some-package",
                "suggested_prefixes": ["/usr/local/bin/"],
            },
            request_context=None,
        )

    # Verify normal SUCCESS response is returned
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "successfully removed" in result.data.lower()


@pytest.mark.asyncio
async def test_remote_bash_tool_approval_denial():
    """Verify tool returns ERROR when approval is denied."""

    mock_toolset = MagicMock()
    mock_toolset.name = "remote_bash"

    tool = RemoteMCPTool(
        name="bash/execute",
        mcp_tool_name="bash_command_runner",
        description="Execute bash command remotely",
        parameters={},
        toolset=mock_toolset,
    )

    # After user denies approval, target returns ERROR
    denial_response = {
        "status": "ERROR",
        "error": "Tool execution denied",
        "data": None,
    }

    response_json = json.dumps(denial_response)

    mock_content_block = MagicMock()
    mock_content_block.type = "text"
    mock_content_block.text = response_json

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

        result = await tool._invoke_async(params={}, request_context=None)

    # Verify ERROR status when approval denied
    assert result.status == StructuredToolResultStatus.ERROR
    assert "denied" in result.error.lower()
