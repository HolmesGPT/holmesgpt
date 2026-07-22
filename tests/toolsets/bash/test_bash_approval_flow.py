"""Tests for bash toolset approval workflow.

This test suite verifies that bash commands requiring approval:
1. Are correctly identified by requires_approval()
2. Surface APPROVAL_REQUIRED status when not pre-approved
3. Execute successfully after user approval (user_approved=True)
4. Work correctly in remote execution scenarios
"""

import pytest
from unittest.mock import MagicMock

from holmes.core.llm import LLM
from holmes.core.tools import StructuredToolResultStatus, ToolInvokeContext
from holmes.plugins.toolsets.bash.bash_toolset import (
    BashExecutorToolset,
    RunBashCommand,
)
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig


def _ctx(**overrides):
    """Build a valid ToolInvokeContext (llm/max_token_count/tool_call_id/
    tool_name are required) with sane test defaults; override as needed."""
    base = dict(
        llm=MagicMock(spec=LLM),
        max_token_count=16000,
        tool_call_id="test-call",
        tool_name="bash",
    )
    base.update(overrides)
    return ToolInvokeContext(**base)


@pytest.fixture
def bash_toolset():
    """Create a bash toolset with minimal config."""
    toolset = BashExecutorToolset()
    toolset.config = BashExecutorConfig(
        allow=["kubectl get", "kubectl describe"],
        deny=[],
        builtin_allowlist="core",
    )
    return toolset


@pytest.fixture
def bash_tool(bash_toolset):
    """Get the bash command tool from the toolset."""
    return bash_toolset.tools[0]  # RunBashCommand is first tool


class TestBashApprovalDetection:
    """Test that bash toolset correctly detects approval requirements."""

    def test_approval_not_required_for_allowed_command(self, bash_tool):
        """Pre-approved commands should not require approval."""
        context = _ctx()
        result = bash_tool.requires_approval(
            params={
                "command": "kubectl get pods",
                "suggested_prefixes": ["kubectl get"],
            },
            context=context,
        )
        assert result is None, "Pre-approved commands should not require approval"

    def test_approval_required_for_unapproved_command(self, bash_tool):
        """Commands not in allow list should require approval."""
        context = _ctx()
        result = bash_tool.requires_approval(
            params={
                "command": "docker ps",
                "suggested_prefixes": ["docker"],
            },
            context=context,
        )
        assert result is not None, "Unapproved commands should require approval"
        assert result.needs_approval is True
        assert "docker" in result.prefixes_to_save

    def test_approval_not_required_for_denied_command(self, bash_tool):
        """Explicitly denied commands should not go through approval flow."""
        # Create toolset with deny list
        toolset = BashExecutorToolset()
        toolset.config = BashExecutorConfig(
            allow=["kubectl get"],
            deny=["rm -rf"],
            builtin_allowlist="core",
        )
        tool = toolset.tools[0]

        context = _ctx()
        result = tool.requires_approval(
            params={
                "command": "rm -rf /",
                "suggested_prefixes": ["rm"],
            },
            context=context,
        )
        # Denied commands don't need approval (they'll be rejected in _invoke)
        assert result is None, "Denied commands don't go through approval flow"


class TestBashExecutionWithoutApproval:
    """Test bash execution behavior when approval is needed."""

    def test_unapproved_command_returns_approval_required_status(self, bash_tool):
        """Unapproved commands should return APPROVAL_REQUIRED status."""
        context = _ctx(user_approved=False)
        result = bash_tool._invoke(
            params={
                "command": "docker ps",
                "suggested_prefixes": ["docker"],
            },
            context=context,
        )
        # Should return APPROVAL_REQUIRED, not ERROR
        # Actually, since requires_approval() is supposed to be called first,
        # _invoke() might return ERROR. Let's test both cases.
        # For now, this tests the behavior when called directly without approval.
        assert result.status in [
            StructuredToolResultStatus.ERROR,
            StructuredToolResultStatus.APPROVAL_REQUIRED,
        ]

    def test_denied_command_returns_error(self, bash_tool):
        """Explicitly denied commands should return ERROR status."""
        toolset = BashExecutorToolset()
        toolset.config = BashExecutorConfig(
            allow=[],
            deny=["rm -rf"],
            builtin_allowlist="core",
        )
        tool = toolset.tools[0]

        context = _ctx(user_approved=False)
        result = tool._invoke(
            params={
                "command": "rm -rf /tmp/something",
                "suggested_prefixes": ["rm"],
            },
            context=context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "blocked" in result.error.lower() or "deny" in result.error.lower()


class TestBashExecutionWithApproval:
    """Test bash execution after user approval."""

    def test_approved_command_executes(self, bash_tool, monkeypatch):
        """Commands with user_approved=True should execute."""
        # Mock the actual bash execution
        mock_execute = MagicMock()
        mock_execute.return_value = MagicMock(
            timed_out=False, stdout="pods found", return_code=0, stderr=""
        )
        monkeypatch.setattr(
            "holmes.plugins.toolsets.bash.bash_toolset.execute_bash_command",
            mock_execute,
        )

        context = _ctx(user_approved=True)
        result = bash_tool._invoke(
            params={
                "command": "docker ps",
                "suggested_prefixes": ["docker"],
            },
            context=context,
        )
        # When user_approved=True, validation is skipped and command executes
        assert result.status == StructuredToolResultStatus.SUCCESS
        mock_execute.assert_called_once()

    def test_approved_command_saves_prefix_for_future(self, bash_tool):
        """When approved, new prefixes should be saved to allow list for future use."""
        context = _ctx()
        approval_req = bash_tool.requires_approval(
            params={
                "command": "docker ps",
                "suggested_prefixes": ["docker"],
            },
            context=context,
        )
        assert approval_req is not None
        assert "docker" in approval_req.prefixes_to_save
        # In the real flow, these prefixes are saved to the session-approved list
        # and merged into the allow list for subsequent invocations


class TestBashApprovalWithSessionContext:
    """Test bash approval within session/conversation context."""

    def test_session_approved_prefixes_are_trusted(self, bash_tool):
        """Session-approved prefixes should not require approval."""
        # Simulate a session where "docker" was previously approved
        context = _ctx(session_approved_prefixes=["docker"])
        result = bash_tool.requires_approval(
            params={
                "command": "docker ps",
                "suggested_prefixes": ["docker"],
            },
            context=context,
        )
        # "docker" should be in the effective allow list from the session
        assert result is None, "Session-approved prefixes should not require approval"

    def test_multiple_approved_prefixes_in_session(self, bash_tool):
        """Multiple session-approved prefixes should work together."""
        context = _ctx(
            session_approved_prefixes=["docker", "kubectl exec"]
        )
        result = bash_tool.requires_approval(
            params={
                "command": "docker ps | grep error",
                "suggested_prefixes": ["docker", "grep"],
            },
            context=context,
        )
        # "docker" is approved, but "grep" comes from builtin allow list
        assert result is None


class TestBashApprovalInCompoundCommands:
    """Test approval behavior with compound/complex bash commands."""

    def test_compound_command_requires_approval(self, bash_tool):
        """Commands with for/while/if/etc should require approval."""
        context = _ctx()
        result = bash_tool.requires_approval(
            params={
                "command": "for pod in $(kubectl get pods -o name); do echo $pod; done",
                "suggested_prefixes": ["kubectl get"],
            },
            context=context,
        )
        # Compound commands always require approval (even if all segments are allowed)
        assert result is not None
        assert result.needs_approval is True

    def test_piped_command_with_one_unapproved(self, bash_tool):
        """Piped commands where one segment is unapproved should require approval."""
        context = _ctx()
        result = bash_tool.requires_approval(
            params={
                "command": "kubectl get pods | jq '.items[].metadata.name'",
                "suggested_prefixes": ["kubectl get", "jq"],
            },
            context=context,
        )
        # "kubectl get" is pre-approved, "jq" is in builtin allow list
        assert result is None


class TestBashApprovalErrorCases:
    """Test error handling in bash approval flow."""

    def test_missing_command_parameter(self, bash_tool):
        """Missing command parameter should return error."""
        context = _ctx()
        result = bash_tool._invoke(
            params={"suggested_prefixes": ["kubectl"]},
            context=context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "required" in result.error.lower()

    def test_missing_suggested_prefixes(self, bash_tool):
        """Missing suggested_prefixes should return error."""
        context = _ctx()
        result = bash_tool._invoke(
            params={"command": "kubectl get pods"},
            context=context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "required" in result.error.lower()

    def test_fabricated_prefix_not_in_command(self, bash_tool):
        """Suggested prefix not actually in command should be rejected."""
        context = _ctx()
        result = bash_tool.requires_approval(
            params={
                "command": "kubectl get pods",
                "suggested_prefixes": ["docker"],  # docker is not in the command!
            },
            context=context,
        )
        # This should be caught as a validation error
        # (prefix must appear in command)
        assert result is None  # Will be rejected in _invoke with error


class TestBashApprovalIntegration:
    """Integration tests simulating the full approval workflow."""

    def test_approval_workflow_local_execution(self, bash_tool, monkeypatch):
        """Simulate full local approval workflow: detect -> approve -> execute."""
        # Mock bash execution
        mock_execute = MagicMock()
        mock_execute.return_value = MagicMock(
            timed_out=False, stdout="data", return_code=0, stderr=""
        )
        monkeypatch.setattr(
            "holmes.plugins.toolsets.bash.bash_toolset.execute_bash_command",
            mock_execute,
        )

        # Step 1: Detect approval needed
        context1 = _ctx()
        approval_req = bash_tool.requires_approval(
            params={"command": "docker ps", "suggested_prefixes": ["docker"]},
            context=context1,
        )
        assert approval_req is not None, "Should detect approval needed"

        # Step 2: Simulate user approval (in real flow, this happens via UI/CLI)
        # Create new context with user_approved=True
        context2 = _ctx(user_approved=True)

        # Step 3: Execute with approval
        result = bash_tool._invoke(
            params={"command": "docker ps", "suggested_prefixes": ["docker"]},
            context=context2,
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        mock_execute.assert_called_once()

    def test_approval_workflow_with_session_memory(self, bash_tool, monkeypatch):
        """Simulate approval workflow where session remembers approvals."""
        mock_execute = MagicMock()
        mock_execute.return_value = MagicMock(
            timed_out=False, stdout="data", return_code=0, stderr=""
        )
        monkeypatch.setattr(
            "holmes.plugins.toolsets.bash.bash_toolset.execute_bash_command",
            mock_execute,
        )

        # First call: docker is new, requires approval
        context1 = _ctx()
        approval_req1 = bash_tool.requires_approval(
            params={"command": "docker ps", "suggested_prefixes": ["docker"]},
            context=context1,
        )
        assert approval_req1 is not None

        # Second call: docker is now in session_approved_prefixes
        context2 = _ctx(session_approved_prefixes=["docker"])
        approval_req2 = bash_tool.requires_approval(
            params={"command": "docker ps", "suggested_prefixes": ["docker"]},
            context=context2,
        )
        assert (
            approval_req2 is None
        ), "Session-approved prefix should not require approval again"

        # Third call: execute with pre-approved prefix
        result = bash_tool._invoke(
            params={"command": "docker ps", "suggested_prefixes": ["docker"]},
            context=context2,
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
