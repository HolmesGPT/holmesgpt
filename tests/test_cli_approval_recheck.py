"""In the interactive CLI a pending call skips the prompt only when a prefix
saved earlier in the same batch now allows it. A call gated by
approval_required_tools, by an MCP server, or by relay for a remote cluster is
never treated as already approved (ROB-1671 review)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from holmes.core.llm import LLM
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import (
    ApprovalRequirement,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
)


class _Tool(Tool):
    def _invoke(self, params, context):
        return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data="ran")

    def get_parameterized_one_liner(self, params):
        return self.name


class _PrefixTool(_Tool):
    allowed: bool = False

    def requires_approval(self, params, context):
        if self.allowed:
            return None
        return ApprovalRequirement(needs_approval=True, reason="prefix not allowed")


def _ask(tool, params):
    llm = MagicMock(spec=LLM)
    llm.get_max_token_count_for_single_tool.return_value = 1000
    ai = ToolCallingLLM.__new__(ToolCallingLLM)
    ai.tool_executor = MagicMock()
    ai.tool_executor.get_tool_by_name.return_value = tool
    ai.llm = llm
    callback = MagicMock(return_value=(True, None))
    ai._prompt_for_approval_decisions(
        [{"tool_call_id": "1", "tool_name": tool.name, "description": "d", "params": params}],
        callback,
    )
    return callback.called


def test_a_tool_gated_by_approval_required_tools_is_asked():
    tool = _Tool(name="run_kubectl_command", description="d", parameters={})
    object.__setattr__(tool, "toolset", SimpleNamespace(approval_required_tools=["run_kubectl_command"]))

    assert _ask(tool, {"args": ["delete", "ns", "prod"]})


@pytest.mark.parametrize("params", [{}, {"command": "kubectl delete pod x"}])
def test_a_call_without_prefixes_is_asked(params):
    """An MCP server or relay asked for approval; nothing local can answer."""
    assert _ask(_Tool(name="run", description="d", parameters={}), params)


def test_a_remote_call_is_asked():
    tool = _PrefixTool(name="remote_bash", description="d", parameters={}, allowed=True)
    object.__setattr__(tool, "is_remote", True)

    assert _ask(tool, {"command": "kubectl get pods", "suggested_prefixes": ["kubectl get"]})


def test_a_prefix_saved_earlier_in_the_batch_skips_the_prompt():
    tool = _PrefixTool(name="bash", description="d", parameters={}, allowed=True)

    assert not _ask(tool, {"command": "kubectl get pods", "suggested_prefixes": ["kubectl get"]})


def test_a_prefix_not_yet_allowed_is_asked():
    tool = _PrefixTool(name="bash", description="d", parameters={}, allowed=False)

    assert _ask(tool, {"command": "rm -rf /tmp/x", "suggested_prefixes": ["rm"]})
