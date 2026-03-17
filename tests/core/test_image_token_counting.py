"""Tests that image tokens are properly counted toward the context window.

litellm's token_counter() currently returns a fixed ~85 tokens per image
regardless of size. The actual API charges are much higher:
  - OpenAI: 85 + 170*tiles (e.g., 800x400 = 255 tokens)
  - Anthropic: (width*height)/750 (e.g., 800x400 = 427 tokens)

These tests verify that our token counting reflects the real cost of images
so that compaction triggers at the right time. They are expected to FAIL
until we fix the image token counting.
"""

import base64
import struct
import zlib

import litellm

from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus


def _make_png(width: int, height: int) -> bytes:
    """Create a minimal valid PNG of the given dimensions."""
    # IHDR chunk
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    # IDAT chunk (single row of black pixels, minimal)
    scanline = b"\x00" + b"\x00\x00\x00" * width
    raw = zlib.compress(scanline)
    idat_crc = zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF
    idat = struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", idat_crc)
    # IEND chunk
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def _make_image_data_url(width: int, height: int) -> str:
    png_bytes = _make_png(width, height)
    b64 = base64.b64encode(png_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def _make_multimodal_tool_message(
    n_images: int, image_width: int = 800, image_height: int = 400
) -> dict:
    """Create a tool message with text + N images, like grafana_render_panel returns."""
    data_url = _make_image_data_url(image_width, image_height)
    content: list[dict] = [{"type": "text", "text": "Rendered panel screenshot."}]
    for _ in range(n_images):
        content.append(
            {"type": "image_url", "image_url": {"url": data_url, "detail": "auto"}}
        )
    return {
        "role": "tool",
        "tool_call_id": "call_render_1",
        "name": "grafana_render_panel",
        "content": content,
    }


class TestImageTokenCounting:
    """Tests that image tokens are counted at realistic levels, not a fixed 85 per image."""

    def test_single_image_token_count_is_realistic(self):
        """A single 800x400 image should count as more than 85 tokens.

        Actual costs:
          - OpenAI (auto/high): ~255 tokens (2 tiles)
          - Anthropic: ~427 tokens (800*400/750)
          - Minimum realistic: 200 tokens

        litellm currently returns ~106 (85 for image + ~21 for text).
        """
        msg = _make_multimodal_tool_message(
            n_images=1, image_width=800, image_height=400
        )
        token_count = litellm.token_counter(model="gpt-4o", messages=[msg])

        text_only_msg = {
            "role": "tool",
            "tool_call_id": "call_render_1",
            "name": "grafana_render_panel",
            "content": "Rendered panel screenshot.",
        }
        text_tokens = litellm.token_counter(model="gpt-4o", messages=[text_only_msg])

        image_tokens = token_count - text_tokens
        # An 800x400 image should cost at least 200 tokens, not 85
        assert image_tokens >= 200, (
            f"Image token count is {image_tokens}, expected >= 200. "
            f"litellm is undercounting image tokens (likely returning fixed 85)."
        )

    def test_large_image_costs_more_than_small_image(self):
        """A 2400x4000 image should cost significantly more tokens than an 800x400 image.

        Actual costs:
          - 800x400: ~255 (OpenAI) / ~427 (Anthropic)
          - 2400x4000: ~1105 (OpenAI) / ~1600 (Anthropic, capped after resize)

        litellm currently returns the same ~85 for both.
        """
        small_msg = _make_multimodal_tool_message(
            n_images=1, image_width=800, image_height=400
        )
        large_msg = _make_multimodal_tool_message(
            n_images=1, image_width=2400, image_height=4000
        )

        small_tokens = litellm.token_counter(model="gpt-4o", messages=[small_msg])
        large_tokens = litellm.token_counter(model="gpt-4o", messages=[large_msg])

        # The large image should cost at least 2x the small image's tokens
        assert large_tokens > small_tokens * 1.5, (
            f"Large image ({large_tokens} tokens) should cost significantly more than "
            f"small image ({small_tokens} tokens). litellm is returning the same count for both."
        )

    def test_multiple_images_accumulate_realistic_tokens(self):
        """10 rendered panels should accumulate a realistic token count.

        With 10 x 800x400 images:
          - Expected: 10 * ~300 = ~3000 tokens minimum
          - litellm currently returns: 10 * 85 + text = ~870

        This matters for compaction: if Holmes thinks 10 renders cost 870 tokens
        but the API charges 3000+, compaction won't trigger when it should.
        """
        msg_10_images = _make_multimodal_tool_message(
            n_images=10, image_width=800, image_height=400
        )
        tokens = litellm.token_counter(model="gpt-4o", messages=[msg_10_images])

        # 10 images should cost at least 2000 tokens total (including text)
        assert tokens >= 2000, (
            f"10 images counted as only {tokens} tokens, expected >= 2000. "
            f"This means compaction will trigger too late when rendering many panels."
        )

    def test_as_tool_call_message_with_images_counts_properly(self):
        """Test the full path: StructuredToolResult with images -> message -> token count.

        This tests the exact code path used in production when grafana_render_panel
        returns a screenshot.
        """
        b64_data = base64.b64encode(_make_png(800, 400)).decode("utf-8")
        data_url = f"data:image/png;base64,{b64_data}"

        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="Rendered screenshot of panel 1 from dashboard test-dash.",
            images=[{"url": data_url, "detail": "auto"}],
        )
        tool_call_result = ToolCallResult(
            tool_call_id="call_1",
            tool_name="grafana_render_panel",
            description="Render panel",
            result=result,
        )

        message = tool_call_result.as_tool_call_message()
        # Message should be multimodal (list content)
        assert isinstance(
            message["content"], list
        ), "Message with images should have list content"

        token_count = litellm.token_counter(model="gpt-4o", messages=[message])

        # The full message (text + 1 image) should be at least 200 tokens
        assert token_count >= 200, (
            f"Full tool message with image counted as only {token_count} tokens. "
            f"Expected >= 200 for realistic image token accounting."
        )
