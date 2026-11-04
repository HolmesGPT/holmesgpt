"""An approved tool call that still comes back approval_required did not run.
Holmes must say so: passed through as-is, the model read it as done and told
the user a remote taint was applied when nothing ran (ROB-1671)."""

import json
from unittest.mock import MagicMock

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
    ai = ToolCallingLLM(
        tool_executor=MagicMock(), max_steps=5, llm=MagicMock(), tool_results_dir=None
    )
    args = json.dumps({"args": ["taint", "node", "n1", "k=v:NoSchedule"], "agent_name": "c2"})

    def invoke(*, tool_to_call, **kwargs):
        return ToolCallResult(
            tool_call_id=tool_to_call.id,
            tool_name=tool_to_call.function.name,
            description="remote_run_kubectl_command taint node n1",
            result=StructuredToolResult(status=result_status, error=error, data=None if error else "tainted"),
        )

    ai._invoke_llm_tool_call = MagicMock(side_effect=invoke)
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
    msgs, events = ai._execute_tool_decisions(
        messages=messages,
        tool_decisions=[ToolApprovalDecision(tool_call_id="tc1", approved=True)],
    )
    [result] = [e.data for e in events if e.event == StreamEvents.TOOL_RESULT]
    tool_message = next(m for m in msgs if m.get("role") == "tool")
    return result, json.dumps(tool_message)


def test_an_approved_call_that_still_needs_approval_is_reported_as_not_run():
    result, tool_message = _run(
        StructuredToolResultStatus.APPROVAL_REQUIRED,
        "Tool 'run_kubectl_command' matches approval pattern 'run_kubectl_command'",
    )

    assert result["result"]["status"] == StructuredToolResultStatus.ERROR.value
    assert "did not run" in result["result"]["error"]
    assert "did not run" in tool_message


def test_an_approved_call_that_ran_is_passed_through():
    result, _ = _run(StructuredToolResultStatus.SUCCESS)

    assert result["result"]["status"] == StructuredToolResultStatus.SUCCESS.value
