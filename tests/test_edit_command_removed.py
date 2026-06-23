"""Regression: `edit_command` was removed from the tool-approval flow.

This test pins the backwards-compat guarantee:

1. Older clients that still POST `edit_command` in their `tool_decisions`
   payload are silently accepted - Pydantic v2's default `extra="ignore"`
   drops the unknown field at deserialization. No 422, no warning.
2. The executed tool sees the ORIGINAL command from the assistant
   tool_call. The edit substitution is gone - the LLM-proposed command
   is what runs.
"""

import json
from unittest.mock import MagicMock

from holmes.core.models import ToolApprovalDecision, ToolCallResult
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus


def _build_ai() -> ToolCallingLLM:
    return ToolCallingLLM(
        tool_executor=MagicMock(),
        max_steps=5,
        llm=MagicMock(),
        tool_results_dir=None,
    )


def _make_messages(tool_call_id: str, original_command: str) -> list:
    return [
        {"role": "user", "content": "do something"},
        {
            "role": "assistant",
            "content": "I'll run a command",
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps({"command": original_command}),
                    },
                    "pending_approval": True,
                }
            ],
        },
    ]


def test_edit_command_in_payload_is_silently_dropped_by_pydantic():
    """An old client still sending `edit_command` must not break — Pydantic
    drops the unknown field at the boundary."""
    raw_payload = {
        "tool_call_id": "tc1",
        "approved": True,
        "edit_command": "rm -rf /tmp/foo",
    }
    decision = ToolApprovalDecision.model_validate(raw_payload)

    assert decision.tool_call_id == "tc1"
    assert decision.approved is True
    assert not hasattr(decision, "edit_command")


def test_original_command_runs_even_when_edit_command_was_sent():
    """An old client POSTing `edit_command="rm -rf /tmp/foo"` over an
    approved `command="ls"` tool_call must see `ls` run — not the
    substituted command. The edit substitution is gone."""
    ai = _build_ai()
    original = "ls"
    messages = _make_messages("tc1", original)

    captured = {}

    def fake_invoke(*, tool_to_call, **kwargs):
        captured["arguments"] = tool_to_call.function.arguments
        params = json.loads(tool_to_call.function.arguments)
        return ToolCallResult(
            tool_call_id=tool_to_call.id,
            tool_name=tool_to_call.function.name,
            description="mocked",
            result=StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data="ok",
                params=params,
            ),
        )

    ai._invoke_llm_tool_call = MagicMock(side_effect=fake_invoke)

    decision = ToolApprovalDecision.model_validate(
        {
            "tool_call_id": "tc1",
            "approved": True,
            "edit_command": "rm -rf /tmp/foo",
        }
    )
    ai._execute_tool_decisions(messages=messages, tool_decisions=[decision])

    assert "arguments" in captured, "_invoke_llm_tool_call was not called"
    assert json.loads(captured["arguments"])["command"] == original
