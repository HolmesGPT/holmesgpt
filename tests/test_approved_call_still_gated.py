"""An approved tool call that still comes back approval_required did not run.
Holmes must say so: passed through as-is, the model read it as done and told
the user a remote taint was applied when nothing ran (ROB-1671)."""

import json
from unittest.mock import MagicMock, patch

import pytest

import holmes.utils.approval_tokens as approval_tokens
from holmes.core.models import ToolApprovalDecision, ToolCallResult
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.utils.stream import StreamEvents


@pytest.fixture(autouse=True)
def stable_signing_key(monkeypatch):
    monkeypatch.setattr(approval_tokens, "SIGNING_KEY", b"\x42" * 32)


def _run(result_status, error=None):
    tool_executor = MagicMock()
    tool_executor.get_toolset_name.return_value = "robusta_platform_mcp"
    tool_executor.get_tool_by_name.return_value.get_parameterized_one_liner.return_value = (
        "remote_run_kubectl_command taint node n1"
    )
    ai = ToolCallingLLM(
        tool_executor=tool_executor, max_steps=5, llm=MagicMock(), tool_results_dir=None
    )
    args = json.dumps({"args": ["taint", "node", "n1", "k=v:NoSchedule"], "agent_name": "c2"})
    ai._directly_invoke_tool_call = MagicMock(
        return_value=StructuredToolResult(
            status=result_status, error=error, data=None if error else "tainted"
        )
    )
    messages = [
        {"role": "user", "content": "taint n1 on c2"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "remote_run_kubectl_command", "arguments": args},
                    "pending_approval": True,
                    "approval_token": approval_tokens.mint_token(
                        "tc1", "remote_run_kubectl_command", args
                    ),
                }
            ],
        },
    ]
    trace_span = MagicMock()
    tool_span = trace_span.start_span.return_value.__enter__.return_value
    with patch("holmes.core.tool_calling_llm.spill_oversized_tool_result", return_value=0):
        msgs, events = ai._execute_tool_decisions(
            messages=messages,
            tool_decisions=[ToolApprovalDecision(tool_call_id="tc1", approved=True)],
            trace_span=trace_span,
        )
    [result] = [e.data for e in events if e.event == StreamEvents.TOOL_RESULT]
    tool_message = next(m for m in msgs if m.get("role") == "tool")
    [span_status] = [
        c.kwargs["metadata"]["holmesgpt.tool.status"]
        for c in tool_span.log.call_args_list
        if "holmesgpt.tool.status" in c.kwargs.get("metadata", {})
    ]
    return result, json.dumps(tool_message), span_status


def test_an_approved_call_that_still_needs_approval_is_reported_as_not_run():
    result, tool_message, span_status = _run(
        StructuredToolResultStatus.APPROVAL_REQUIRED,
        "Tool 'run_kubectl_command' matches approval pattern 'run_kubectl_command'",
    )

    assert result["result"]["status"] == StructuredToolResultStatus.ERROR.value
    assert "did not run" in result["result"]["error"]
    assert "did not run" in tool_message
    assert span_status == StructuredToolResultStatus.ERROR.value


def test_an_approved_call_that_ran_is_passed_through():
    result, _, span_status = _run(StructuredToolResultStatus.SUCCESS)

    assert result["result"]["status"] == StructuredToolResultStatus.SUCCESS.value
    assert span_status == StructuredToolResultStatus.SUCCESS.value
