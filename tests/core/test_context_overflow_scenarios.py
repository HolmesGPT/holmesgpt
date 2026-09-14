"""
TDD Red Tests for Context Window Overflow Scenarios

These tests expose bugs that can cause the context window to be exceeded,
resulting in errors like:
    litellm.ContextWindowExceededError: litellm.BadRequestError: AnthropicError -
    "prompt is too long: 214764 tokens > 200000 maximum"

The key scenarios that cause overflow:
1. Large runbook content returned from fetch_runbook tool (no size limit on DB content)
2. Large global instructions from DAL (unbounded)
3. Large runbook catalog in user prompt (unbounded)
4. Accumulated assistant messages in long conversations

These are TDD "red" tests - they are expected to FAIL with the current
implementation, demonstrating bugs that need to be fixed.
"""

import pytest

from holmes.core.llm import TokenCountMetadata
from holmes.core.truncation.input_context_window_limiter import (
    truncate_messages_to_fit_context,
)
from holmes.utils.global_instructions import Instructions, generate_runbooks_args
from holmes.plugins.runbooks import RobustaRunbookInstruction, RunbookCatalog


def char_based_token_counter(messages) -> TokenCountMetadata:
    """Token counter that uses character count as proxy (1 char = 1 token)."""
    total = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    total += len(item.get("text", ""))
    return TokenCountMetadata(
        total_tokens=total,
        system_tokens=0,
        tools_to_call_tokens=0,
        tools_tokens=0,
        user_tokens=0,
        assistant_tokens=0,
        other_tokens=0,
    )


class TestGlobalInstructionsOverflow:
    """Tests for global instructions causing context overflow.

    The get_global_instructions_for_account() method in SupabaseDal fetches
    instructions without any size limit. Large instructions can cause the
    user prompt to exceed the context window.
    """

    def test_large_global_instructions_should_be_truncated(self):
        """
        When global instructions are very large, they should be truncated
        to prevent context window overflow.

        Current behavior: Instructions are included in full, potentially
        causing "prompt is too long" errors.
        """
        # Create very large global instructions (100K characters ~ 25K tokens)
        large_instruction = "Check the following systems: " + ", ".join(
            [f"system_{i}" for i in range(10000)]
        )
        global_instructions = Instructions(instructions=[large_instruction])

        # Generate runbooks args which includes global instructions in user prompt
        result = generate_runbooks_args(
            runbook_catalog=None,
            global_instructions=global_instructions,
        )

        # The global instructions block should be limited to prevent overflow
        global_block = result.get("global_instructions", "")

        # With a typical 200K token context and ~4 chars per token,
        # global instructions should not exceed ~200K chars to leave room
        # for system prompt, issue data, and tool results
        MAX_GLOBAL_INSTRUCTIONS_CHARS = 50000  # Conservative limit

        assert len(global_block) <= MAX_GLOBAL_INSTRUCTIONS_CHARS, (
            f"Global instructions too large: {len(global_block)} chars. "
            f"This can cause context window overflow."
        )

    def test_multiple_global_instructions_should_be_limited(self):
        """
        When there are many global instructions, the total size should be limited.
        """
        # Create many instructions that together exceed context limits
        instructions = [f"Always check component_{i} for issues." for i in range(5000)]
        global_instructions = Instructions(instructions=instructions)

        result = generate_runbooks_args(
            runbook_catalog=None,
            global_instructions=global_instructions,
        )

        global_block = result.get("global_instructions", "")
        MAX_GLOBAL_INSTRUCTIONS_CHARS = 50000

        assert len(global_block) <= MAX_GLOBAL_INSTRUCTIONS_CHARS, (
            f"Combined global instructions too large: {len(global_block)} chars"
        )


class TestRunbookCatalogOverflow:
    """Tests for runbook catalog causing context overflow.

    The runbook catalog is included in the user prompt via generate_runbooks_args().
    When there are many runbooks in the catalog, the prompt can become very large.
    """

    def test_large_runbook_catalog_should_be_truncated(self):
        """
        When there are many runbooks in the catalog, the catalog string
        should be truncated to prevent context window overflow.
        """
        # Create a catalog with many runbooks (each runbook adds to prompt size)
        runbooks = []
        for i in range(500):
            runbooks.append(
                RobustaRunbookInstruction(
                    id=f"runbook-{i:04d}",
                    symptom=f"When the system exhibits symptom type {i} which includes "
                            f"error codes ERR-{i:04d} and related issues in component_{i}",
                    title=f"Troubleshooting Guide for Component {i} Issues",
                    instruction=None,  # Instruction is fetched separately
                    alerts=[f"AlertName{i}", f"WarningType{i}"],
                )
            )

        catalog = RunbookCatalog(catalog=runbooks)
        result = generate_runbooks_args(runbook_catalog=catalog)

        catalog_str = result.get("runbook_catalog", "")

        # Catalog should be limited to prevent overwhelming the context
        MAX_CATALOG_CHARS = 50000

        assert len(catalog_str) <= MAX_CATALOG_CHARS, (
            f"Runbook catalog too large: {len(catalog_str)} chars. "
            f"This can cause context window overflow when combined with "
            f"system prompt, issue data, and global instructions."
        )


class TestAccumulatedMessagesOverflow:
    """Tests for accumulated messages causing context overflow.

    The truncate_messages_to_fit_context function only truncates tool messages.
    Non-tool messages (system, user, assistant) can accumulate and exceed the
    context window, especially in long conversations.
    """

    def test_accumulated_assistant_messages_should_be_handled(self):
        """
        When there are many assistant messages (from multiple LLM iterations),
        they should be handled to prevent context overflow.

        Current behavior: Only tool messages are truncated. Assistant messages
        accumulate without limit, eventually causing:
        "The combined size of system_prompt and user_prompt (...tokens)
        exceeds the maximum context size"
        """
        # Simulate a conversation with many iterations
        messages = [
            {"role": "system", "content": "You are a helpful assistant." * 100},
            {"role": "user", "content": "Help me diagnose this issue." * 100},
        ]

        # Add many assistant messages (simulating multiple LLM iterations)
        # Each iteration adds an assistant message with tool_calls
        for i in range(20):
            messages.append({
                "role": "assistant",
                "content": f"I'll investigate step {i}. " * 50,
                "tool_calls": [{
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": f"tool_{i}", "arguments": "{}"}
                }]
            })
            # Add corresponding tool result
            messages.append({
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "name": f"tool_{i}",
                "content": f"Result {i}: " + "data " * 100,
            })

        # This should not raise an exception - it should handle the overflow
        max_context_size = 10000
        maximum_output_token = 1000

        try:
            result = truncate_messages_to_fit_context(
                messages,
                max_context_size,
                maximum_output_token,
                char_based_token_counter,
            )
            # If we get here, verify the result fits
            total_tokens = char_based_token_counter(result.truncated_messages)
            assert total_tokens.total_tokens + maximum_output_token <= max_context_size
        except Exception as e:
            if "exceeds the maximum context size" in str(e):
                pytest.fail(
                    f"Context overflow not handled: {e}. "
                    f"Non-tool messages should be compacted or truncated."
                )
            raise


class TestCombinedOverflowScenario:
    """Test realistic combined scenario that causes overflow."""

    def test_non_tool_messages_overflow_should_be_handled(self):
        """
        When non-tool messages (system + user + assistant) exceed the context
        window, the system should handle it gracefully instead of raising
        an exception.

        Current behavior: An exception is raised:
        "The combined size of system_prompt and user_prompt (...tokens)
        exceeds the maximum context size"

        Expected: The system should compact/truncate non-tool messages too.
        """
        # Create messages where system + user + assistant exceed context
        # WITHOUT relying on tool message truncation
        system_prompt_size = 120000  # Large system prompt
        user_prompt_size = 100000    # Large user prompt

        messages = [
            {"role": "system", "content": "S" * system_prompt_size},
            {"role": "user", "content": "U" * user_prompt_size},
            # No tool messages - just system + user exceeding limit
        ]

        max_context_size = 200000  # 200K tokens (Anthropic limit)
        maximum_output_token = 16000  # Reserved for output

        # This currently raises an exception instead of handling the overflow
        # Expected: Should truncate/compact system and user messages
        try:
            result = truncate_messages_to_fit_context(
                messages,
                max_context_size,
                maximum_output_token,
                char_based_token_counter,
            )
            # If we get here, verify the result fits
            total = char_based_token_counter(result.truncated_messages).total_tokens
            assert total + maximum_output_token <= max_context_size
        except Exception as e:
            if "exceeds the maximum context size" in str(e):
                pytest.fail(
                    f"Non-tool overflow not handled gracefully. Error: {e}\n"
                    f"The system should compact/truncate non-tool messages "
                    f"instead of raising an exception."
                )
            raise

    def test_tool_truncation_works_when_non_tool_messages_fit(self):
        """
        Verify tool truncation works correctly when non-tool messages
        fit within the context window.

        This is a regression test to ensure the existing truncation
        mechanism continues to work.
        """
        system_prompt_size = 50000
        user_prompt_size = 30000

        messages = [
            {"role": "system", "content": "S" * system_prompt_size},
            {"role": "user", "content": "U" * user_prompt_size},
        ]

        # Add tool messages that need truncation
        for i in range(5):
            messages.append({
                "role": "assistant",
                "content": f"Step {i}...",
                "tool_calls": [{"id": f"call_{i}", "type": "function",
                               "function": {"name": f"tool_{i}", "arguments": "{}"}}]
            })
            messages.append({
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "name": f"tool_{i}",
                "content": "T" * 50000,  # Large tool result
            })

        max_context_size = 200000
        maximum_output_token = 16000

        # This should work - tool messages get truncated
        result = truncate_messages_to_fit_context(
            messages,
            max_context_size,
            maximum_output_token,
            char_based_token_counter,
        )

        total = char_based_token_counter(result.truncated_messages).total_tokens
        assert total + maximum_output_token <= max_context_size, (
            f"Tool truncation failed: {total + maximum_output_token} > {max_context_size}"
        )
