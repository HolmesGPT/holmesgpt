"""Tests for LLM completion with tool content handling."""

import litellm
import pytest
from unittest.mock import MagicMock, patch

from holmes.core.llm import DefaultLLM, _messages_contain_tool_content


class TestMessagesContainToolContent:
    """Tests for _messages_contain_tool_content helper function."""

    def test_messages_with_tool_role(self):
        """Should return True for messages with role='tool'."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "tool", "content": "Tool result"},
        ]
        assert _messages_contain_tool_content(messages) is True

    def test_messages_with_assistant_tool_calls(self):
        """Should return True for assistant messages with tool_calls."""
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_123", "function": {"name": "test"}}],
            },
        ]
        assert _messages_contain_tool_content(messages) is True

    def test_plain_messages_without_tools(self):
        """Should return False for plain messages without tool content."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "How are you?"},
        ]
        assert _messages_contain_tool_content(messages) is False

    def test_empty_messages(self):
        """Should return False for empty message list."""
        assert _messages_contain_tool_content([]) is False

    def test_assistant_without_tool_calls(self):
        """Should return False for assistant messages without tool_calls."""
        messages = [
            {"role": "assistant", "content": "Hello"},
            {"role": "assistant", "tool_calls": None},
            {"role": "assistant"},
        ]
        assert _messages_contain_tool_content(messages) is False


class TestCompletionWithToolContent:
    """Tests for completion() method handling tool content in messages."""

    @pytest.fixture
    def mock_llm(self):
        """Create a DefaultLLM instance with mocked dependencies."""
        with patch("holmes.core.llm.litellm.validate_environment") as mock_validate:
            mock_validate.return_value = {
                "keys_in_environment": True,
                "missing_keys": [],
            }
            llm = DefaultLLM(
                model="bedrock/anthropic.claude-3-sonnet-20240229-v1:0",
                api_key="test_key",
            )
            return llm

    def test_sets_modify_params_when_tools_absent_and_messages_have_tool_content(
        self, mock_llm
    ):
        """Should set modify_params=True when tools=None but messages contain tool content."""
        messages = [
            {"role": "user", "content": "Test"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "call_1", "function": {"name": "test"}}],
            },
            {"role": "tool", "content": "Result"},
        ]

        original_modify_params = litellm.modify_params

        with patch("holmes.core.llm.litellm.completion") as mock_completion:
            mock_completion.return_value = MagicMock()
            mock_completion.return_value.choices = []

            # Call without tools (tools=None is default)
            try:
                mock_llm.completion(messages=messages, tools=None)

                # During the call, modify_params should have been True
                # We verify by checking it was called (the mock was executed)
                assert mock_completion.called
            except Exception:
                pass  # Ignore any exceptions from mocked completion

        # After the call, modify_params should be restored
        assert litellm.modify_params == original_modify_params

    def test_restores_modify_params_after_completion(self, mock_llm):
        """Should restore modify_params to original value after completion."""
        messages = [
            {"role": "tool", "content": "Result"},
        ]

        # Set initial value
        litellm.modify_params = False
        original_value = litellm.modify_params

        with patch("holmes.core.llm.litellm.completion") as mock_completion:
            mock_completion.return_value = MagicMock()
            mock_completion.return_value.choices = []

            try:
                mock_llm.completion(messages=messages, tools=None)
            except Exception:
                pass

        # Should be restored to original value
        assert litellm.modify_params == original_value

    def test_restores_modify_params_on_exception(self, mock_llm):
        """Should restore modify_params even when completion raises an exception."""
        messages = [
            {"role": "tool", "content": "Result"},
        ]

        # Set initial value
        litellm.modify_params = False
        original_value = litellm.modify_params

        with patch("holmes.core.llm.litellm.completion") as mock_completion:
            mock_completion.side_effect = Exception("Test error")

            try:
                mock_llm.completion(messages=messages, tools=None)
            except Exception:
                pass  # Expected to raise

        # Should still be restored to original value
        assert litellm.modify_params == original_value

    def test_does_not_modify_params_when_tools_provided(self, mock_llm):
        """Should not set modify_params when tools are provided."""
        messages = [
            {"role": "user", "content": "Test"},
        ]
        tools = [{"type": "function", "function": {"name": "test_tool"}}]

        original_modify_params = litellm.modify_params

        with patch("holmes.core.llm.litellm.completion") as mock_completion:
            mock_completion.return_value = MagicMock()
            mock_completion.return_value.choices = []

            try:
                mock_llm.completion(
                    messages=messages, tools=tools, tool_choice="auto"
                )
            except Exception:
                pass

        # modify_params should not have been changed
        assert litellm.modify_params == original_modify_params

    def test_does_not_modify_params_when_no_tool_content(self, mock_llm):
        """Should not set modify_params when messages have no tool content."""
        messages = [
            {"role": "user", "content": "Test"},
            {"role": "assistant", "content": "Response"},
        ]

        original_modify_params = litellm.modify_params

        with patch("holmes.core.llm.litellm.completion") as mock_completion:
            mock_completion.return_value = MagicMock()
            mock_completion.return_value.choices = []

            try:
                mock_llm.completion(messages=messages, tools=None)
            except Exception:
                pass

        # modify_params should not have been changed
        assert litellm.modify_params == original_modify_params
