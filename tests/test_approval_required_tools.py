"""Tool-name-based approval gating (replaces the removed restricted_tools mechanism).

These verify that `approval_required_tools` on a toolset is what gates a tool
behind human approval — the basis for the Kubernetes Remediation MCP addon, where
only `run_kubectl_command` prompts and the read-only tools do not.
"""

from typing import Any, Dict

from unittest.mock import MagicMock

from holmes.core.llm import LLM
from holmes.core.tools import (
    Tool,
    ToolInvokeContext,
    Toolset,
    ToolsetTag,
    ToolsetYamlFromConfig,
)
from holmes.core.models import StructuredToolResult, StructuredToolResultStatus


class _EchoTool(Tool):
    """Minimal concrete tool that always succeeds when actually invoked."""

    toolset: Any = None

    def _invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS, data="ok", params=params
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{self.name}({params})"


# Mirrors the Kubernetes Remediation MCP tool set: four auto-approved read-only
# tools plus the single approval-gated mutating fallback.
READ_ONLY_TOOLS = [
    "read_file_from_container",
    "run_preapproved_kubectl_command",
    "run_preapproved_diagnostic_image",
    "get_remediation_mcp_config",
]
APPROVAL_TOOL = "run_kubectl_command"


def _build_toolset() -> Toolset:
    tools = [
        _EchoTool(name=name, description=name)
        for name in READ_ONLY_TOOLS + [APPROVAL_TOOL]
    ]
    toolset = Toolset(
        name="kubernetes_remediation",
        description="test",
        tools=tools,
        tags=[ToolsetTag.CORE],
        approval_required_tools=[APPROVAL_TOOL],
    )
    # Wire the back-reference the approval check reads.
    for tool in toolset.tools:
        tool.toolset = toolset
    return toolset


def _context(tool_name: str, user_approved: bool = False) -> ToolInvokeContext:
    return ToolInvokeContext(
        user_approved=user_approved,
        llm=MagicMock(spec=LLM),
        max_token_count=10000,
        tool_call_id="call_1",
        tool_name=tool_name,
    )


def _tool(toolset: Toolset, name: str) -> Tool:
    return next(t for t in toolset.tools if t.name == name)


def test_approval_tool_requires_approval():
    toolset = _build_toolset()
    result = _tool(toolset, APPROVAL_TOOL).invoke({}, _context(APPROVAL_TOOL))
    assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED


def test_read_only_tools_do_not_require_approval():
    toolset = _build_toolset()
    for name in READ_ONLY_TOOLS:
        result = _tool(toolset, name).invoke({}, _context(name))
        assert result.status == StructuredToolResultStatus.SUCCESS, name


def test_session_approval_suppresses_reprompt():
    toolset = _build_toolset()
    result = _tool(toolset, APPROVAL_TOOL).invoke(
        {}, _context(APPROVAL_TOOL, user_approved=True)
    )
    assert result.status == StructuredToolResultStatus.SUCCESS


def test_deprecated_restricted_tools_is_ignored_with_warning(caplog):
    """Old configs carrying the removed restricted_tools key load without error."""
    with caplog.at_level("WARNING"):
        ts = ToolsetYamlFromConfig(
            name="kubernetes_remediation",
            restricted_tools=["*"],
            approval_required_tools=[APPROVAL_TOOL],
        )

    assert not hasattr(ts, "restricted_tools")
    assert ts.approval_required_tools == [APPROVAL_TOOL]
    assert any("restricted_tools" in r.message for r in caplog.records)
