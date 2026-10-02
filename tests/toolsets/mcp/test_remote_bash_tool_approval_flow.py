"""Parser tests for how the caller's RemoteMCPTool handles the responses a
remote bash tool produces across the approval flow.

These exercise the caller-side MCP parser (`RemoteMCPTool._invoke_async`) only:
given the JSON a remote executor returns (via relay), assert the parser maps it
to the right `StructuredToolResult`. The target-side behavior (returning
APPROVAL_REQUIRED, running once approved) is covered by
`tests/core/conversations_worker/test_tool_call_worker.py`.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPTool, RemoteMCPToolset


def _session_returning(payload: dict) -> AsyncMock:
    block = MagicMock(type="text", text=json.dumps(payload))
    result_obj = MagicMock(content=[block], isError=False)
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=result_obj)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


@pytest.mark.asyncio
async def test_parses_approval_required_and_surfaces_caller_params():
    """A remote 'approval_required' response is mapped to APPROVAL_REQUIRED.

    Relay does not forward the executor's approval params back to the caller,
    so the parser falls back to the caller's own input params — those are what
    the approval UI/Slack prompt is built from. Assert the exact params, not
    just non-None.
    """
    tool = RemoteMCPTool(
        name="remote_bash",
        mcp_tool_name="bash",
        description="Execute bash command remotely",
        parameters={},
        toolset=MagicMock(spec=RemoteMCPToolset),
        is_remote=True,
    )

    approval_response = {
        "status": "approval_required",
        "error": "Command requires approval. New prefixes: /usr/local/bin",
        "data": None,
    }
    input_params = {
        "command": "rm -rf /usr/local/bin/some-package",
        "agent_name": "eu-eks-prod-2",
    }

    with patch(
        "holmes.plugins.toolsets.mcp.toolset_mcp.get_initialized_mcp_session"
    ) as get_session:
        get_session.return_value = _session_returning(approval_response)
        result = await tool._invoke_async(params=input_params, request_context=None)

    assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED
    assert "Command requires approval" in result.error
    assert result.params == input_params


@pytest.mark.asyncio
async def test_parses_success_response_after_approval():
    """After approval the executor runs the tool; a normal success response
    is parsed back to SUCCESS with the command output."""
    tool = RemoteMCPTool(
        name="remote_bash",
        mcp_tool_name="bash",
        description="Execute bash command remotely",
        parameters={},
        toolset=MagicMock(spec=RemoteMCPToolset),
        is_remote=True,
    )

    final_response = {
        "status": "success",
        "data": "Package successfully removed from /usr/local/bin/",
        "error": None,
    }

    with patch(
        "holmes.plugins.toolsets.mcp.toolset_mcp.get_initialized_mcp_session"
    ) as get_session:
        get_session.return_value = _session_returning(final_response)
        result = await tool._invoke_async(
            params={"command": "rm -rf /usr/local/bin/some-package"},
            request_context=None,
        )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "successfully removed" in result.data.lower()
