"""
Tests for Tool Approval and Restriction mechanisms.

These tests verify the two orthogonal security mechanisms:
1. Tool Approval: Requires user confirmation before executing certain tools
2. Tool Restriction: Limits when LLM can use certain tools (requires runbook or explicit flag)
"""

import pytest
from unittest.mock import MagicMock, patch

from holmes.core.tools import (
    ApprovalRequirement,
    RestrictionResult,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetStatusEnum,
    ToolsetTag,
    YAMLTool,
    YAMLToolset,
)
from holmes.core.llm import LLM


# =============================================================================
# Test Fixtures
# =============================================================================


class SimpleTool(Tool):
    """A simple test tool that always succeeds."""

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=f"Executed with params: {params}",
            params=params,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"simple_tool({params})"


class ApprovalRequiredTool(Tool):
    """A tool that always requires approval."""

    def requires_approval(
        self, params: dict, context: ToolInvokeContext
    ) -> ApprovalRequirement:
        return ApprovalRequirement(
            needs_approval=True,
            reason="This tool always requires approval for testing",
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="Approval required tool executed",
            params=params,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return "approval_required_tool()"


class ConditionalApprovalTool(Tool):
    """A tool that requires approval only for certain params."""

    def requires_approval(
        self, params: dict, context: ToolInvokeContext
    ) -> ApprovalRequirement:
        if params.get("dangerous", False):
            return ApprovalRequirement(
                needs_approval=True,
                reason="Dangerous operation requires approval",
            )
        return None

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=f"Conditional tool executed: dangerous={params.get('dangerous')}",
            params=params,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"conditional_tool(dangerous={params.get('dangerous', False)})"


@pytest.fixture
def mock_llm():
    """Create a mock LLM for testing."""
    llm = MagicMock(spec=LLM)
    llm.get_max_token_count_for_single_tool.return_value = 10000
    return llm


@pytest.fixture
def base_context(mock_llm):
    """Create a base ToolInvokeContext for testing."""
    return ToolInvokeContext(
        tool_number=1,
        user_approved=False,
        llm=mock_llm,
        max_token_count=10000,
        tool_call_id="test-call-id",
        tool_name="test_tool",
        restricted_tools_enabled=False,
        runbook_in_use=False,
    )


@pytest.fixture
def simple_toolset():
    """Create a simple toolset with test tools."""
    return YAMLToolset(
        name="test_toolset",
        description="Test toolset for approval/restriction testing",
        tags=[ToolsetTag.CORE],
        tools=[],
    )


# =============================================================================
# ApprovalRequirement Class Tests
# =============================================================================


class TestApprovalRequirement:
    """Tests for the ApprovalRequirement class."""

    def test_approval_required_true(self):
        """Test creating an approval requirement that needs approval."""
        requirement = ApprovalRequirement(
            needs_approval=True,
            reason="Test reason for approval",
        )
        assert requirement.needs_approval is True
        assert requirement.reason == "Test reason for approval"

    def test_approval_required_false(self):
        """Test creating an approval requirement that doesn't need approval."""
        requirement = ApprovalRequirement(
            needs_approval=False,
            reason="",
        )
        assert requirement.needs_approval is False
        assert requirement.reason == ""

    def test_default_reason_is_empty(self):
        """Test that the default reason is an empty string."""
        requirement = ApprovalRequirement(needs_approval=True)
        assert requirement.reason == ""


# =============================================================================
# RestrictionResult Class Tests
# =============================================================================


class TestRestrictionResult:
    """Tests for the RestrictionResult class."""

    def test_authorized_true(self):
        """Test creating a restriction result that is authorized."""
        result = RestrictionResult(authorized=True)
        assert result.authorized is True
        assert result.reason == ""

    def test_authorized_false_with_reason(self):
        """Test creating a restriction result that is not authorized."""
        result = RestrictionResult(
            authorized=False,
            reason="Tool is restricted and requires runbook authorization",
        )
        assert result.authorized is False
        assert "restricted" in result.reason.lower()


# =============================================================================
# Tool Approval Tests
# =============================================================================


class TestToolApproval:
    """Tests for tool approval mechanisms."""

    def test_tool_without_approval_logic_returns_none(self, base_context):
        """Test that a tool without requires_approval() returns None."""
        tool = SimpleTool(
            name="simple_tool",
            description="A simple tool",
        )
        result = tool.requires_approval({}, base_context)
        assert result is None

    def test_tool_with_approval_always_required(self, base_context):
        """Test a tool that always requires approval."""
        tool = ApprovalRequiredTool(
            name="approval_tool",
            description="A tool requiring approval",
        )
        result = tool.requires_approval({}, base_context)
        assert result is not None
        assert result.needs_approval is True
        assert "always requires approval" in result.reason

    def test_conditional_approval_safe_params(self, base_context):
        """Test conditional approval with safe parameters."""
        tool = ConditionalApprovalTool(
            name="conditional_tool",
            description="A conditionally approved tool",
            parameters={
                "dangerous": ToolParameter(
                    description="Whether operation is dangerous",
                    type="boolean",
                    required=False,
                )
            },
        )
        result = tool.requires_approval({"dangerous": False}, base_context)
        assert result is None

    def test_conditional_approval_dangerous_params(self, base_context):
        """Test conditional approval with dangerous parameters."""
        tool = ConditionalApprovalTool(
            name="conditional_tool",
            description="A conditionally approved tool",
            parameters={
                "dangerous": ToolParameter(
                    description="Whether operation is dangerous",
                    type="boolean",
                    required=False,
                )
            },
        )
        result = tool.requires_approval({"dangerous": True}, base_context)
        assert result is not None
        assert result.needs_approval is True
        assert "dangerous" in result.reason.lower()


class TestToolsetApprovalConfig:
    """Tests for toolset-level approval configuration."""

    def test_approval_required_tools_pattern_match(self, base_context):
        """Test that approval_required_tools patterns are matched."""
        toolset = YAMLToolset(
            name="test_toolset",
            description="Test toolset",
            tags=[ToolsetTag.CORE],
            tools=[
                YAMLTool(
                    name="kubectl_delete",
                    description="Delete resources",
                    command="kubectl delete {{ resource }}",
                ),
            ],
            approval_required_tools=["kubectl_*"],
        )

        tool = toolset.tools[0]
        # Use object.__setattr__ to bypass Pydantic validation for dynamic attribute
        object.__setattr__(tool, "toolset", toolset)

        approval = tool._check_approval_config()
        assert approval is not None
        assert approval.needs_approval is True
        assert "kubectl_*" in approval.reason

    def test_approval_required_tools_no_match(self, base_context):
        """Test that non-matching tools don't require approval."""
        toolset = YAMLToolset(
            name="test_toolset",
            description="Test toolset",
            tags=[ToolsetTag.CORE],
            tools=[
                YAMLTool(
                    name="list_pods",
                    description="List pods",
                    command="kubectl get pods",
                ),
            ],
            approval_required_tools=["kubectl_delete_*"],
        )

        tool = toolset.tools[0]
        # Use object.__setattr__ to bypass Pydantic validation for dynamic attribute
        object.__setattr__(tool, "toolset", toolset)

        approval = tool._check_approval_config()
        assert approval is None

    def test_wildcard_approval_pattern(self, base_context):
        """Test that '*' pattern in approval_required_tools affects all tools."""
        toolset = YAMLToolset(
            name="test_toolset",
            description="Test toolset",
            tags=[ToolsetTag.CORE],
            tools=[
                YAMLTool(
                    name="any_tool",
                    description="Any tool",
                    command="echo hello",
                ),
            ],
            approval_required_tools=["*"],
        )

        tool = toolset.tools[0]
        # Use object.__setattr__ to bypass Pydantic validation for dynamic attribute
        object.__setattr__(tool, "toolset", toolset)

        approval = tool._check_approval_config()
        assert approval is not None
        assert approval.needs_approval is True
        assert "'*'" in approval.reason


class TestToolInvokeApproval:
    """Tests for approval checks during tool invocation."""

    def test_invoke_returns_approval_required_status(self, base_context):
        """Test that invoking a tool requiring approval returns APPROVAL_REQUIRED status."""
        tool = ApprovalRequiredTool(
            name="approval_tool",
            description="A tool requiring approval",
        )

        result = tool.invoke({}, base_context)

        assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED
        assert "always requires approval" in result.error

    def test_invoke_with_user_approved_skips_approval_check(self, mock_llm):
        """Test that user_approved=True skips approval check."""
        tool = ApprovalRequiredTool(
            name="approval_tool",
            description="A tool requiring approval",
        )

        context = ToolInvokeContext(
            tool_number=1,
            user_approved=True,  # User already approved
            llm=mock_llm,
            max_token_count=10000,
            tool_call_id="test-call-id",
            tool_name="approval_tool",
            restricted_tools_enabled=False,
            runbook_in_use=False,
        )

        result = tool.invoke({}, context)

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "executed" in result.data.lower()


# =============================================================================
# Tool Restriction Tests
# =============================================================================


class TestToolRestriction:
    """Tests for tool restriction mechanisms."""

    def test_tool_restricted_field(self):
        """Test the restricted field on a tool."""
        tool = SimpleTool(
            name="restricted_tool",
            description="A restricted tool",
            restricted=True,
        )
        assert tool.restricted is True
        assert tool._is_restricted() is True

    def test_tool_not_restricted_by_default(self):
        """Test that tools are not restricted by default."""
        tool = SimpleTool(
            name="normal_tool",
            description="A normal tool",
        )
        assert tool.restricted is False
        assert tool._is_restricted() is False

    def test_toolset_restricted_tools_pattern(self):
        """Test toolset-level restricted_tools pattern matching."""
        toolset = YAMLToolset(
            name="test_toolset",
            description="Test toolset",
            tags=[ToolsetTag.CORE],
            tools=[
                YAMLTool(
                    name="kubectl_delete_pod",
                    description="Delete a pod",
                    command="kubectl delete pod {{ pod_name }}",
                ),
            ],
            restricted_tools=["kubectl_delete_*"],
        )

        tool = toolset.tools[0]
        # Use object.__setattr__ to bypass Pydantic validation for dynamic attribute
        object.__setattr__(tool, "toolset", toolset)

        assert tool._is_restricted() is True

    def test_toolset_restricted_tools_no_match(self):
        """Test that non-matching tools are not restricted."""
        toolset = YAMLToolset(
            name="test_toolset",
            description="Test toolset",
            tags=[ToolsetTag.CORE],
            tools=[
                YAMLTool(
                    name="kubectl_get_pods",
                    description="Get pods",
                    command="kubectl get pods",
                ),
            ],
            restricted_tools=["kubectl_delete_*"],
        )

        tool = toolset.tools[0]
        # Use object.__setattr__ to bypass Pydantic validation for dynamic attribute
        object.__setattr__(tool, "toolset", toolset)

        assert tool._is_restricted() is False


class TestRestrictionFiltering:
    """Tests for restriction filtering at the tools list level.

    Note: Restriction is enforced by filtering tools from the tools list,
    not by blocking at invocation time. These tests verify the filtering logic.
    """

    def test_restricted_tool_filtered_from_tools_list(self):
        """Test that restricted tools are filtered when include_restricted=False."""
        from holmes.core.tools_utils.tool_executor import ToolExecutor

        # Create a toolset with both restricted and non-restricted tools
        toolset = YAMLToolset(
            name="test_toolset",
            description="Test toolset",
            tags=[ToolsetTag.CORE],
            tools=[
                YAMLTool(
                    name="normal_tool",
                    description="A normal tool",
                    command="echo normal",
                ),
                YAMLTool(
                    name="restricted_tool",
                    description="A restricted tool",
                    command="echo restricted",
                    restricted=True,
                ),
            ],
        )
        toolset.status = ToolsetStatusEnum.ENABLED

        executor = ToolExecutor([toolset])

        # With include_restricted=True, both tools should be present
        all_tools = executor.get_all_tools_openai_format(
            target_model="gpt-4", include_restricted=True
        )
        assert len(all_tools) == 2

        # With include_restricted=False, only the normal tool should be present
        filtered_tools = executor.get_all_tools_openai_format(
            target_model="gpt-4", include_restricted=False
        )
        assert len(filtered_tools) == 1
        assert filtered_tools[0]["function"]["name"] == "normal_tool"

    def test_restricted_tool_invocation_succeeds(self, base_context):
        """Test that restricted tools can be invoked (filtering is at list level)."""
        # Restriction is at the tools list level, not at invocation time.
        # If a tool is invoked, it should succeed regardless of restriction.
        tool = SimpleTool(
            name="restricted_tool",
            description="A restricted tool",
            restricted=True,
        )

        result = tool.invoke({}, base_context)

        # Tool should execute successfully - filtering happens at list level
        assert result.status == StructuredToolResultStatus.SUCCESS


class TestRestrictedToolDescription:
    """Tests for [RESTRICTED] prefix in tool descriptions."""

    def test_restricted_tool_gets_prefix_in_openai_format(self):
        """Test that restricted tools get [RESTRICTED] prefix in OpenAI format."""
        tool = SimpleTool(
            name="restricted_tool",
            description="Delete all resources",
            restricted=True,
        )

        openai_format = tool.get_openai_format(target_model="gpt-4")

        assert "[RESTRICTED]" in openai_format["function"]["description"]
        assert openai_format["function"]["description"].startswith("[RESTRICTED]")

    def test_non_restricted_tool_no_prefix(self):
        """Test that non-restricted tools don't get the prefix."""
        tool = SimpleTool(
            name="normal_tool",
            description="List all resources",
            restricted=False,
        )

        openai_format = tool.get_openai_format(target_model="gpt-4")

        assert "[RESTRICTED]" not in openai_format["function"]["description"]


# =============================================================================
# Combined Approval and Restriction Tests
# =============================================================================


class TestApprovalAndRestrictionCombined:
    """Tests for tools that are both restricted AND require approval.

    Note: Restriction is enforced at the tools list level (filtering),
    while approval is enforced at invocation time.
    """

    def test_restricted_tool_with_approval_still_requires_approval(self, base_context):
        """Test that restricted tools with approval requirements still check approval.

        Restriction filtering happens at the tools list level.
        If a tool is invoked (meaning it was in the list), approval is still checked.
        """
        tool = ApprovalRequiredTool(
            name="restricted_approval_tool",
            description="A tool that is restricted and requires approval",
            restricted=True,
        )

        # Tool is invoked (passed the filter), but approval is still required
        result = tool.invoke({}, base_context)

        # Should get approval required (restriction is at list level, not invocation)
        assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED

    def test_approval_checked_when_tool_invoked(self, mock_llm):
        """Test that approval is checked when a restricted tool is invoked."""
        tool = ApprovalRequiredTool(
            name="restricted_approval_tool",
            description="A tool that is restricted and requires approval",
            restricted=True,
        )

        context = ToolInvokeContext(
            tool_number=1,
            user_approved=False,
            llm=mock_llm,
            max_token_count=10000,
            tool_call_id="test-call-id",
            tool_name="restricted_approval_tool",
            restricted_tools_enabled=False,
            runbook_in_use=True,  # Tool is in the list (runbook enables it)
        )

        result = tool.invoke({}, context)

        # Approval should still be required
        assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED

    def test_user_approved_bypasses_both(self, mock_llm):
        """Test that user_approved=True bypasses both restriction and approval."""
        tool = ApprovalRequiredTool(
            name="restricted_approval_tool",
            description="A tool that is restricted and requires approval",
            restricted=True,
        )

        context = ToolInvokeContext(
            tool_number=1,
            user_approved=True,  # User approved
            llm=mock_llm,
            max_token_count=10000,
            tool_call_id="test-call-id",
            tool_name="restricted_approval_tool",
            restricted_tools_enabled=False,
            runbook_in_use=False,
        )

        result = tool.invoke({}, context)

        assert result.status == StructuredToolResultStatus.SUCCESS


# =============================================================================
# Toolset Configuration Validation Tests
# =============================================================================


class TestToolsetConfigValidation:
    """Tests for toolset configuration validation."""

    def test_toolset_with_approval_and_restriction_config(self):
        """Test creating a toolset with both approval and restriction configs."""
        toolset = YAMLToolset(
            name="secure_toolset",
            description="A secure toolset with approval and restrictions",
            tags=[ToolsetTag.CORE],
            tools=[
                YAMLTool(
                    name="safe_read",
                    description="Read operation",
                    command="cat {{ file }}",
                ),
                YAMLTool(
                    name="dangerous_write",
                    description="Write operation",
                    command="echo {{ content }} > {{ file }}",
                ),
                YAMLTool(
                    name="very_dangerous_delete",
                    description="Delete operation",
                    command="rm {{ file }}",
                ),
            ],
            approval_required_tools=["dangerous_*", "very_dangerous_*"],
            restricted_tools=["very_dangerous_*"],
        )

        # safe_read: no approval, not restricted
        safe_tool = toolset.tools[0]
        object.__setattr__(safe_tool, "toolset", toolset)
        assert safe_tool._is_restricted() is False
        assert safe_tool._check_approval_config() is None

        # dangerous_write: requires approval, not restricted
        dangerous_tool = toolset.tools[1]
        object.__setattr__(dangerous_tool, "toolset", toolset)
        assert dangerous_tool._is_restricted() is False
        approval = dangerous_tool._check_approval_config()
        assert approval is not None
        assert approval.needs_approval is True

        # very_dangerous_delete: requires approval AND restricted
        very_dangerous_tool = toolset.tools[2]
        object.__setattr__(very_dangerous_tool, "toolset", toolset)
        assert very_dangerous_tool._is_restricted() is True
        approval = very_dangerous_tool._check_approval_config()
        assert approval is not None
        assert approval.needs_approval is True

    def test_empty_patterns_dont_match_anything(self):
        """Test that empty pattern lists don't match any tools."""
        toolset = YAMLToolset(
            name="test_toolset",
            description="Test toolset",
            tags=[ToolsetTag.CORE],
            tools=[
                YAMLTool(
                    name="any_tool",
                    description="Any tool",
                    command="echo hello",
                ),
            ],
            approval_required_tools=[],
            restricted_tools=[],
        )

        tool = toolset.tools[0]
        object.__setattr__(tool, "toolset", toolset)

        assert tool._is_restricted() is False
        assert tool._check_approval_config() is None

    def test_wildcard_pattern_matches_all(self):
        """Test that '*' pattern matches all tools."""
        toolset = YAMLToolset(
            name="test_toolset",
            description="Test toolset",
            tags=[ToolsetTag.CORE],
            tools=[
                YAMLTool(
                    name="any_tool",
                    description="Any tool",
                    command="echo hello",
                ),
            ],
            restricted_tools=["*"],
        )

        tool = toolset.tools[0]
        object.__setattr__(tool, "toolset", toolset)

        assert tool._is_restricted() is True

    def test_wildcard_approval_pattern_matches_all(self):
        """Test that '*' pattern in approval_required_tools matches all tools."""
        toolset = YAMLToolset(
            name="test_toolset",
            description="Test toolset",
            tags=[ToolsetTag.CORE],
            tools=[
                YAMLTool(
                    name="any_tool",
                    description="Any tool",
                    command="echo hello",
                ),
                YAMLTool(
                    name="another_tool",
                    description="Another tool",
                    command="echo world",
                ),
            ],
            approval_required_tools=["*"],
        )

        for tool in toolset.tools:
            object.__setattr__(tool, "toolset", toolset)
            approval = tool._check_approval_config()
            assert approval is not None
            assert approval.needs_approval is True
            assert "'*'" in approval.reason
