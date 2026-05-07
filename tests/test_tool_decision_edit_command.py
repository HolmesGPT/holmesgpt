"""Unit tests for the `edit_command` field on `ToolApprovalDecision`.

When an approved tool decision carries `edit_command`, the worker must:

1. Execute the tool with the edited command instead of the original.
2. Persist the edited command back into the conversation history (the
   assistant message's `tool_calls`) so subsequent turns and the
   ANSWER_END message reflect what actually ran.
3. Emit the edited command in the TOOL_RESULT stream event.
"""

import json
from unittest.mock import MagicMock

from holmes.core.models import ToolApprovalDecision, ToolCallResult
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.utils.stream import StreamEvents


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


def _build_ai() -> ToolCallingLLM:
    return ToolCallingLLM(
        tool_executor=MagicMock(),
        max_steps=5,
        llm=MagicMock(),
        tool_results_dir=None,
    )


def test_edit_command_replaces_command_in_executed_tool_call():
    ai = _build_ai()
    original = "kubectl delete pod dangerous"
    edited = "kubectl get pods -n default"
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

    decision = ToolApprovalDecision(
        tool_call_id="tc1", approved=True, edit_command=edited
    )
    ai._execute_tool_decisions(messages=messages, tool_decisions=[decision])

    assert "arguments" in captured, "_invoke_llm_tool_call was not called"
    assert json.loads(captured["arguments"])["command"] == edited


def test_edit_command_persisted_in_conversation_history():
    ai = _build_ai()
    original = "kubectl delete pod dangerous"
    edited = "kubectl get pods -n default"
    messages = _make_messages("tc1", original)

    def fake_invoke(*, tool_to_call, **kwargs):
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

    decision = ToolApprovalDecision(
        tool_call_id="tc1", approved=True, edit_command=edited
    )
    updated_messages, _events = ai._execute_tool_decisions(
        messages=messages, tool_decisions=[decision]
    )

    assistant_msg = updated_messages[1]
    persisted = json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"])
    assert persisted["command"] == edited
    # And the pending_approval flag was cleared so it isn't replayed next turn.
    assert "pending_approval" not in assistant_msg["tool_calls"][0]


def test_edit_command_appears_in_tool_result_stream_event():
    ai = _build_ai()
    edited = "kubectl get pods -n default"
    messages = _make_messages("tc1", "kubectl delete pod dangerous")

    def fake_invoke(*, tool_to_call, **kwargs):
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

    decision = ToolApprovalDecision(
        tool_call_id="tc1", approved=True, edit_command=edited
    )
    _messages, events = ai._execute_tool_decisions(
        messages=messages, tool_decisions=[decision]
    )

    tool_results = [e for e in events if e.event == StreamEvents.TOOL_RESULT]
    assert len(tool_results) == 1
    assert tool_results[0].data["result"]["params"]["command"] == edited


def test_edit_command_ignored_when_decision_is_rejected():
    """A denied decision must not mutate arguments or run the tool, even if
    edit_command is present."""
    ai = _build_ai()
    original = "kubectl delete pod dangerous"
    messages = _make_messages("tc1", original)

    ai._invoke_llm_tool_call = MagicMock()

    decision = ToolApprovalDecision(
        tool_call_id="tc1",
        approved=False,
        edit_command="kubectl get pods",
        feedback="no",
    )
    updated_messages, events = ai._execute_tool_decisions(
        messages=messages, tool_decisions=[decision]
    )

    # The tool was never invoked.
    ai._invoke_llm_tool_call.assert_not_called()
    # The original command remains in conversation history.
    persisted = json.loads(
        updated_messages[1]["tool_calls"][0]["function"]["arguments"]
    )
    assert persisted["command"] == original
    # A denial TOOL_RESULT was still emitted.
    tool_results = [e for e in events if e.event == StreamEvents.TOOL_RESULT]
    assert len(tool_results) == 1
    assert tool_results[0].data["result"]["status"] == "error"
