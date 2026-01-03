import pytest

from holmes.core.llm import TokenCountMetadata
from holmes.core.truncation.input_context_window_limiter import (
    truncate_messages_to_fit_context,
)


def simple_token_counter(messages) -> TokenCountMetadata:
    """Simple token counter that counts characters for testing."""
    total = 0
    for message in messages:
        total += len(message.get("content", ""))
    return TokenCountMetadata(
        total_tokens=total,
        system_tokens=0,
        tools_to_call_tokens=0,
        tools_tokens=0,
        user_tokens=0,
        assistant_tokens=0,
        other_tokens=0,
    )


class TestTruncateMessagesToFitContext:
    """Test suite for truncate_messages_to_fit_context helper function."""

    def test_truncation_basics(self):
        """Test truncation with different tool size distributions."""

        def create_test_messages(tool_contents):
            """Helper to create test messages with given tool contents."""
            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Please help me with this task."},
                {
                    "role": "assistant",
                    "content": "I'll help you by calling some tools.",
                },
            ]
            for i, content in enumerate(tool_contents):
                messages.append(
                    {
                        "role": "tool",
                        "name": f"tool{i+1}",
                        "content": content,
                        "tool_call_id": f"call_{i+1}",
                        "token_count": len(content),  # Simulate token count
                    }
                )
            return messages

        def verify_truncation_result(messages, result):
            """Helper to verify truncation results."""
            # Verify structure preserved
            assert len(result) == len(messages)
            assert result[0]["role"] == "system"
            assert result[1]["role"] == "user"
            assert result[2]["role"] == "assistant"

            tool_messages = [msg for msg in result if msg["role"] == "tool"]
            assert len(tool_messages) == 3

            # Verify efficient space utilization - i.e. all room available for tools is used
            total_tool_length = sum(len(msg["content"]) for msg in tool_messages)
            assert total_tool_length == available_for_tools

            for tool_msg in tool_messages:
                # if this tool was not truncated
                if "token_count" in tool_msg:
                    assert "TRUNCATED" not in tool_msg["content"]
                else:
                    assert tool_msg["content"].endswith("\n\n[TRUNCATED]")

        # Context limits
        max_context_size = 10000
        maximum_output_token = 2000

        # Calculate available space for tools (same for both cases)
        non_tool_content = "You are a helpful assistant.Please help me with this task.I'll help you by calling some tools."
        available_for_tools = (
            max_context_size - maximum_output_token - len(non_tool_content)
        )

        # Test Case 1: 3 large tools that all need truncation
        messages_case1 = create_test_messages(["A" * 5000, "B" * 6000, "C" * 7000])
        result1 = truncate_messages_to_fit_context(
            messages_case1.copy(),
            max_context_size,
            maximum_output_token,
            simple_token_counter,
        )
        messages = result1.truncated_messages
        verify_truncation_result(messages_case1, messages)

        # Test Case 2: One big tool and two small tools
        messages_case2 = create_test_messages(["X" * 8000, "Y" * 100, "Z" * 200])
        result2 = truncate_messages_to_fit_context(
            messages_case2.copy(),
            max_context_size,
            maximum_output_token,
            simple_token_counter,
        )
        messages = result2.truncated_messages
        verify_truncation_result(messages_case2, messages)

    def test_no_truncation_when_messages_fit(self):
        """Test that messages are not truncated when they fit within context."""
        # Create small messages that fit within context
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User query"},
            {
                "role": "tool",
                "name": "tool1",
                "content": "Small response 1",
                "tool_call_id": "call_1",
            },
            {
                "role": "tool",
                "name": "tool2",
                "content": "Small response 2",
                "tool_call_id": "call_2",
            },
            {
                "role": "tool",
                "name": "tool3",
                "content": "Small response 3",
                "tool_call_id": "call_3",
            },
        ]

        original_messages = [msg.copy() for msg in messages]

        result = truncate_messages_to_fit_context(
            messages,
            max_context_size=10000,
            maximum_output_token=1000,
            count_tokens_fn=simple_token_counter,
        )

        # Messages should remain unchanged
        assert result.truncated_messages == original_messages

    def test_raises_exception_when_non_tool_messages_too_large(self):
        """Test that exception is raised when non-tool messages exceed context."""
        # Create messages where system + user content exceeds available context
        messages = [
            {"role": "system", "content": "A" * 8000},  # Very large system prompt
            {"role": "user", "content": "B" * 3000},  # Large user query
            {
                "role": "tool",
                "name": "tool1",
                "content": "Tool response",
                "tool_call_id": "call_1",
            },
        ]

        with pytest.raises(Exception, match="exceeds the maximum context size"):
            truncate_messages_to_fit_context(
                messages,
                max_context_size=10000,
                maximum_output_token=1000,
                count_tokens_fn=simple_token_counter,
            )

    def test_empty_tool_list(self):
        """Test behavior with no tool messages."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User query"},
            {"role": "assistant", "content": "Assistant response"},
        ]

        original_messages = [msg.copy() for msg in messages]

        result = truncate_messages_to_fit_context(
            messages,
            max_context_size=1000,
            maximum_output_token=100,
            count_tokens_fn=simple_token_counter,
        )

        # Should return unchanged when no tool messages
        assert result.truncated_messages == original_messages


class TestTruncateMessagesToFitContextEdgeCases:
    def test_truncation_notice_fits_exactly(self):
        """Test when allocated space is exactly the size of the truncation notice."""
        truncation_notice = "\n\n[TRUNCATED]"
        # The tool message is longer than the available space, but available space == len(truncation_notice)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
            {
                "role": "tool",
                "name": "tool1",
                "content": "A" * 100,
                "tool_call_id": "call_1",
            },
        ]
        # Only enough space for the truncation notice
        maximum_output_token = 10
        max_context_size = len("sysusr") + len(truncation_notice) + maximum_output_token

        def count_tokens_fn(msgs):
            total = sum(len(m.get("content", "")) for m in msgs)
            return TokenCountMetadata(
                total_tokens=total,
                system_tokens=0,
                tools_to_call_tokens=0,
                tools_tokens=0,
                user_tokens=0,
                assistant_tokens=0,
                other_tokens=0,
            )

        result = truncate_messages_to_fit_context(
            messages.copy(),
            max_context_size,
            maximum_output_token,
            count_tokens_fn,
        )
        messages = result.truncated_messages
        tool_msg = [m for m in messages if m["role"] == "tool"][0]
        assert tool_msg["content"] == truncation_notice

    def test_truncation_notice_larger_than_allocation(self):
        """Test when allocated space is less than the truncation notice length."""
        truncation_notice = "\n\n[TRUNCATED]"
        # Only 5 chars available for tool message
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
            {
                "role": "tool",
                "name": "tool1",
                "content": "A" * 100,
                "tool_call_id": "call_1",
            },
        ]
        maximum_output_token = 10
        max_context_size = len("sysusr") + 5 + maximum_output_token

        def count_tokens_fn(msgs):
            total = sum(len(m.get("content", "")) for m in msgs)
            return TokenCountMetadata(
                total_tokens=total,
                system_tokens=0,
                tools_to_call_tokens=0,
                tools_tokens=0,
                user_tokens=0,
                assistant_tokens=0,
                other_tokens=0,
            )

        result = truncate_messages_to_fit_context(
            messages.copy(),
            max_context_size,
            maximum_output_token,
            count_tokens_fn,
        )
        messages = result.truncated_messages
        tool_msg = [m for m in messages if m["role"] == "tool"][0]
        # Should be the first 5 chars of the truncation notice
        assert tool_msg["content"] == truncation_notice[:5]

    def test_multiple_tools_fair_allocation(self):
        """Test that multiple tool messages are truncated fairly."""
        # 2 tool messages, both too large, available space split evenly
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
            {
                "role": "tool",
                "name": "tool1",
                "content": "A" * 100,
                "tool_call_id": "call_1",
            },
            {
                "role": "tool",
                "name": "tool2",
                "content": "B" * 100,
                "tool_call_id": "call_2",
            },
        ]
        # Only 60 chars available for both tools
        max_context_size = len("sysusr") + 60 + 10
        maximum_output_token = 10

        def count_tokens_fn(msgs):
            total = sum(len(m.get("content", "")) for m in msgs)
            return TokenCountMetadata(
                total_tokens=total,
                system_tokens=0,
                tools_to_call_tokens=0,
                tools_tokens=0,
                user_tokens=0,
                assistant_tokens=0,
                other_tokens=0,
            )

        result = truncate_messages_to_fit_context(
            messages.copy(),
            max_context_size,
            maximum_output_token,
            count_tokens_fn,
        )
        messages = result.truncated_messages
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        # Each tool should get 30 chars (60 // 2)
        for msg in tool_msgs:
            assert len(msg["content"]) == 30
