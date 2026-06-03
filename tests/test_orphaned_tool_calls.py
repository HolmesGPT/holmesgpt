"""Tests for auto-denying abandoned (orphaned) tool calls.

When the LLM requests a tool call that requires approval and the user never
decides on it — they close the approval modal or ask a new follow-up question
instead — the conversation history ends up with an assistant `tool_calls`
message that has no matching tool result. The next LLM call then fails because
providers (Anthropic/Bedrock) require every tool_use block to be immediately
followed by a tool_result block:

    `tool_use` ids were found without `tool_result` blocks immediately after

`_resolve_orphaned_tool_calls` fixes this by injecting a denial tool result for
any such abandoned call so the conversation can continue.
"""

import json
from unittest.mock import MagicMock

from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.utils.stream import StreamEvents


def _build_ai() -> ToolCallingLLM:
    return ToolCallingLLM(
        tool_executor=MagicMock(),
        max_steps=5,
        llm=MagicMock(),
        tool_results_dir=None,
    )


def _assistant_tool_call_msg(tool_call_id: str, pending_approval: bool = True) -> dict:
    tool_call = {
        "id": tool_call_id,
        "type": "function",
        "function": {
            "name": "bash",
            "arguments": json.dumps({"command": "kubectl delete pod x"}),
        },
    }
    if pending_approval:
        tool_call["pending_approval"] = True
    return {
        "role": "assistant",
        "content": "I'll run a command",
        "tool_calls": [tool_call],
    }


def test_orphaned_pending_tool_call_gets_denial_result():
    ai = _build_ai()
    messages = [
        {"role": "user", "content": "do something"},
        _assistant_tool_call_msg("tc1"),
        {"role": "user", "content": "actually, never mind — what's the weather?"},
    ]

    updated, events = ai._resolve_orphaned_tool_calls(messages)

    # A tool result was inserted immediately after the assistant tool_calls msg.
    assert updated[2]["role"] == "tool"
    assert updated[2]["tool_call_id"] == "tc1"
    assert "cancelled" in updated[2]["content"]
    # The new user question still follows.
    assert updated[3] == {
        "role": "user",
        "content": "actually, never mind — what's the weather?",
    }
    # The stale pending_approval flag is cleared.
    assert "pending_approval" not in updated[1]["tool_calls"][0]
    # A TOOL_RESULT stream event was emitted for the client.
    assert any(ev.event == StreamEvents.TOOL_RESULT for ev in events)


def test_orphaned_tool_call_without_pending_flag_gets_denial_result():
    # Reproduces the "Approve/Deny then immediately stop" case: the assistant
    # tool_calls message has no pending_approval flag but also no result.
    ai = _build_ai()
    messages = [
        {"role": "user", "content": "do something"},
        _assistant_tool_call_msg("tc1", pending_approval=False),
    ]

    updated, events = ai._resolve_orphaned_tool_calls(messages)

    assert updated[2]["role"] == "tool"
    assert updated[2]["tool_call_id"] == "tc1"
    assert len(events) == 1


def test_resolved_tool_calls_are_left_untouched():
    ai = _build_ai()
    messages = [
        {"role": "user", "content": "do something"},
        _assistant_tool_call_msg("tc1", pending_approval=False),
        {"role": "tool", "tool_call_id": "tc1", "name": "bash", "content": "done"},
    ]

    updated, events = ai._resolve_orphaned_tool_calls(messages)

    assert updated == messages
    assert events == []


def test_multiple_tool_calls_in_one_message_each_get_a_result():
    ai = _build_ai()
    assistant_msg = {
        "role": "assistant",
        "content": "running two commands",
        "tool_calls": [
            {
                "id": "tc1",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "a"})},
                "pending_approval": True,
            },
            {
                "id": "tc2",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "b"})},
                "pending_approval": True,
            },
        ],
    }
    messages = [
        {"role": "user", "content": "do something"},
        assistant_msg,
        {"role": "user", "content": "new question"},
    ]

    updated, events = ai._resolve_orphaned_tool_calls(messages)

    # Both denial results inserted, in order, immediately after the assistant msg.
    assert updated[2]["role"] == "tool" and updated[2]["tool_call_id"] == "tc1"
    assert updated[3]["role"] == "tool" and updated[3]["tool_call_id"] == "tc2"
    assert updated[4] == {"role": "user", "content": "new question"}
    assert len(events) == 2
