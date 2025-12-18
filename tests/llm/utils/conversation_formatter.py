"""Utilities for formatting conversation history as markdown."""

import json
from typing import Any


def format_conversation_as_markdown(conversation_history: list[dict[str, Any]]) -> str:
    """
    Format a conversation history as readable markdown.

    Handles system prompts, user messages, assistant responses (including tool calls),
    and tool responses. Each message is clearly separated and formatted for human readability.

    Args:
        conversation_history: List of message dicts with structure:
            - role: "system" | "user" | "assistant" | "tool"
            - content: Message content (string)
            - tool_calls: Optional list of tool calls (for assistant messages)
            - tool_call_id: Optional tool call ID (for tool messages)
            - name: Optional tool name (for tool messages)
            - reasoning_content: Optional reasoning (for assistant messages)
            - thinking_blocks: Optional thinking blocks (for assistant messages)

    Returns:
        Formatted markdown string with clear message separations.
    """
    markdown_lines = []
    message_count = len(conversation_history)

    for i, msg in enumerate(conversation_history, 1):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        # Add horizontal rule separator between messages (except before first message)
        if i > 1:
            markdown_lines.append("\n---\n")

        # Format based on role
        if role == "system":
            markdown_lines.append(f"## 📋 Message {i}/{message_count}: System Prompt\n")
            markdown_lines.append(_format_system_message(content))

        elif role == "user":
            markdown_lines.append(f"## 👤 Message {i}/{message_count}: User\n")
            markdown_lines.append(f"{content}\n")

        elif role == "assistant":
            markdown_lines.append(f"## 🤖 Message {i}/{message_count}: Assistant\n")
            markdown_lines.append(_format_assistant_message(msg))

        elif role == "tool":
            tool_name = msg.get("name", "unknown_tool")
            tool_call_id = msg.get("tool_call_id", "unknown_id")
            markdown_lines.append(
                f"## 🔧 Message {i}/{message_count}: Tool Response (`{tool_name}`)\n"
            )
            markdown_lines.append(f"**Tool Call ID:** `{tool_call_id}`\n\n")
            markdown_lines.append(_format_tool_response(content))

        else:
            markdown_lines.append(
                f"## ❓ Message {i}/{message_count}: {role.title()}\n"
            )
            markdown_lines.append(f"{content}\n")

    return "".join(markdown_lines)


def _format_system_message(content: str) -> str:
    """Format system message, truncating if very long."""
    if len(content) > 2000:
        return f"```\n{content[:2000]}\n... [TRUNCATED BY TEST FRAMEWORK FOR DISPLAY, {len(content)} total characters]\n```\n"
    return f"```\n{content}\n```\n"


def _format_assistant_message(msg: dict[str, Any]) -> str:
    """Format assistant message including content, reasoning, thinking, and tool calls."""
    lines = []

    # Assistant's main content/response
    content = msg.get("content", "")
    if content:
        lines.append(f"**Response:**\n{content}\n")

    # Reasoning content (if present)
    reasoning = msg.get("reasoning_content", "")
    if reasoning:
        lines.append(f"\n**Reasoning:**\n```\n{reasoning}\n```\n")

    # Thinking blocks (if present)
    thinking_blocks = msg.get("thinking_blocks", [])
    if thinking_blocks:
        lines.append("\n**Thinking:**\n")
        for block in thinking_blocks:
            thinking_text = block.get("thinking", "")
            if thinking_text:
                lines.append(f"```\n{thinking_text}\n```\n")

    # Tool calls (if present)
    tool_calls = msg.get("tool_calls", [])
    if tool_calls:
        lines.append(f"\n**Tool Calls:** ({len(tool_calls)} calls)\n")
        for j, tool_call in enumerate(tool_calls, 1):
            lines.append(_format_tool_call(tool_call, j))

    return "".join(lines)


def _format_tool_call(tool_call: dict[str, Any], index: int) -> str:
    """Format a single tool call."""
    lines = []

    tool_call_id = tool_call.get("id", "unknown")
    tool_type = tool_call.get("type", "function")

    lines.append(f"\n{index}. **Tool Call ID:** `{tool_call_id}` (type: {tool_type})\n")

    # Function details
    function = tool_call.get("function", {})
    if function:
        func_name = function.get("name", "unknown")
        func_args = function.get("arguments", "{}")

        lines.append(f"   - **Function:** `{func_name}`\n")

        # Try to pretty-print JSON arguments
        try:
            if isinstance(func_args, str):
                args_dict = json.loads(func_args)
            else:
                args_dict = func_args

            args_json = json.dumps(args_dict, indent=2)
            lines.append(f"   - **Arguments:**\n```json\n{args_json}\n```\n")
        except (json.JSONDecodeError, TypeError):
            lines.append(f"   - **Arguments:** `{func_args}`\n")

    return "".join(lines)


def _format_tool_response(content: str) -> str:
    """Format tool response content."""
    # Tool responses can be quite long, so we'll format them in a code block
    # and truncate if extremely long
    if len(content) > 5000:
        return f"**Output:**\n```\n{content[:5000]}\n... [TRUNCATED BY TEST FRAMEWORK FOR DISPLAY, {len(content)} total characters]\n```\n"
    return f"**Output:**\n```\n{content}\n```\n"
