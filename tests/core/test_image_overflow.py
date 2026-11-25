"""
TDD Red Tests for Image/Vision Content Context Window Overflow

These tests expose bugs where image (multimodal) content is not properly
handled by the context window management system, leading to:
1. Truncation corrupting multimodal message structure (str() of list content)
2. Truncated content containing useless partial base64 data that wastes tokens
3. Compaction not stripping images before sending to LLM

A base64-encoded 1MB image is ~1.3M characters but only 85-1105 API tokens.
The truncation code does str(list_content) which produces a Python repr string
that is NOT valid API content.
"""

import base64
from unittest.mock import MagicMock, patch

from holmes.core.truncation.compaction import compact_conversation_history
from holmes.core.truncation.input_context_window_limiter import (
    _truncate_tool_message,
)


def _make_fake_base64_image(size_bytes: int = 100_000) -> str:
    """Create a fake base64 data URI of approximately the given size."""
    raw_data = b"x" * size_bytes
    encoded = base64.b64encode(raw_data).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


class TestTruncationCorruptsMultimodalContent:
    """Tests proving _truncate_tool_message corrupts multimodal content.

    _truncate_tool_message() line 33 does:
        original = msg_content if isinstance(msg_content, str) else str(msg_content)

    For multimodal content (list of dicts), str() produces a Python repr string
    like "[{'type': 'text', ...}]" which is NOT valid API content.
    """

    def test_truncated_multimodal_content_is_not_python_repr(self):
        """
        After truncating multimodal content, the result should NOT be a
        Python repr string of a list. This is invalid API content that
        would confuse the LLM.

        Current behavior: str([{'type': 'image_url', ...}]) becomes the content.
        """
        image_data = _make_fake_base64_image(50_000)
        multimodal_content = [
            {"type": "text", "text": "Analysis results: " + "X" * 5000},
            {"type": "image_url", "image_url": {"url": image_data}},
        ]

        msg = {
            "role": "tool",
            "tool_call_id": "call_123",
            "name": "screenshot_tool",
            "content": multimodal_content,
        }

        _truncate_tool_message(msg, allocated_space=1000, needed_space=50000)

        result_content = msg["content"]

        # BUG: The result is a Python repr string starting with "[{"
        # This is NOT valid API content
        assert not (isinstance(result_content, str) and result_content.lstrip().startswith("[{")), (
            f"Truncation corrupted multimodal content into a Python repr string. "
            f"Got: {result_content[:200]}..."
        )

    def test_truncated_multimodal_content_is_valid_type(self):
        """
        After truncation, content should be either:
        - A plain string (text only, without Python repr artifacts)
        - A list of content dicts (valid multimodal format)

        It should NOT be a string that looks like a Python list/dict repr.
        """
        multimodal_content = [
            {"type": "text", "text": "Hello " * 1000},
            {"type": "image_url", "image_url": {"url": _make_fake_base64_image(10_000)}},
        ]

        msg = {
            "role": "tool",
            "tool_call_id": "call_789",
            "name": "analyze_tool",
            "content": multimodal_content,
        }

        _truncate_tool_message(msg, allocated_space=500, needed_space=20000)

        result = msg["content"]

        if isinstance(result, str):
            # If converted to string, it should be clean text, not repr
            assert "{'type':" not in result, (
                f"Content contains Python dict repr artifacts: {result[:200]}..."
            )
            assert "'image_url'" not in result, (
                f"Content contains raw image_url dict key from repr: {result[:200]}..."
            )

    def test_truncated_content_does_not_contain_base64_fragment(self):
        """
        After truncation, the content should NOT contain partial base64 data.
        Partial base64 is invalid and wastes context window tokens on
        undecipherable data that provides no value to the LLM.

        Current behavior: str() of the list includes the full base64 URL,
        then character truncation cuts it mid-stream, leaving a fragment
        like "data:image/jpeg;base64,eHh4eHh4eHh4eHh4eH..."
        """
        image_data = _make_fake_base64_image(100_000)  # Large image
        multimodal_content = [
            {"type": "text", "text": "Short text"},
            {"type": "image_url", "image_url": {"url": image_data}},
        ]

        msg = {
            "role": "tool",
            "tool_call_id": "call_base64",
            "name": "screenshot_tool",
            "content": multimodal_content,
        }

        # Allocate enough space to include some base64 but not all
        _truncate_tool_message(msg, allocated_space=5000, needed_space=140000)

        result = msg["content"]

        # After truncation, there should be no partial base64 data
        # (it's useless and wastes tokens)
        assert isinstance(result, str)  # Current behavior converts to str
        assert "base64," not in result, (
            f"Truncated content contains partial base64 data which wastes tokens. "
            f"Base64 fragments cannot be decoded and provide no value to the LLM. "
            f"Content: {result[:300]}..."
        )


class TestCompactionStripsImages:
    """Tests that conversation compaction strips images before LLM call.

    compact_conversation_history() sends the FULL conversation to an LLM for
    summarization. If messages contain base64 images, this compaction call
    itself can exceed the context window, causing a nested overflow.
    """

    def test_compaction_strips_image_content_from_messages(self):
        """
        Before sending to the compaction LLM, image content should be
        replaced with a placeholder like "[image]" to avoid sending
        huge base64 data to the compaction model.

        Current behavior: The full conversation including base64 image data
        is sent to the LLM for compaction.
        """
        image_data = _make_fake_base64_image(200_000)  # ~267K chars

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": [
                {"type": "text", "text": "What's in this image?"},
                {"type": "image_url", "image_url": {"url": image_data}},
            ]},
            {"role": "assistant", "content": "I can see a diagram."},
            {"role": "user", "content": "Tell me more."},
            {"role": "assistant", "content": "The diagram shows a system architecture."},
        ]

        # Track what messages are sent to the LLM completion call
        captured_messages = []

        mock_llm = MagicMock()
        mock_llm.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                model_dump=lambda **kwargs: {"role": "assistant", "content": "Compacted summary."}
            ))]
        )

        def capture_completion(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return mock_llm.completion.return_value

        mock_llm.completion.side_effect = capture_completion

        with patch("holmes.core.truncation.compaction.litellm"):
            compact_conversation_history(messages, mock_llm)

        # Check if any message sent to the compaction LLM contains base64 image data
        total_image_chars_in_compaction = 0
        for msg in captured_messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        total_image_chars_in_compaction += len(url)
            elif isinstance(content, str) and "base64," in content:
                total_image_chars_in_compaction += len(content)

        assert total_image_chars_in_compaction == 0, (
            f"Compaction sent {total_image_chars_in_compaction} chars of image data "
            f"to the LLM. Images should be stripped/replaced with placeholders "
            f"before compaction to prevent nested context overflow."
        )
