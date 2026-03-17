import base64
from unittest.mock import patch

import pytest

from holmes.core.llm import (
    DefaultLLM,
    _anthropic_image_token_count,
    _get_image_dimensions,
    is_anthropic_model,
)


IMG_WIDTH = 100
IMG_HEIGHT = 200

# litellm's default per-image estimate that we must NOT use for Anthropic
_LITELLM_DEFAULT_IMAGE_TOKENS = 85


def _make_png_data_uri(width: int, height: int) -> str:
    """Create a valid PNG data URI with the given dimensions."""
    import struct
    import zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        raw = chunk_type + data
        return struct.pack(">I", len(data)) + raw + struct.pack(">I", zlib.crc32(raw) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    # color_type=2 (RGB), bit_depth=8
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    # Each row: filter byte (0) + width * 3 bytes (RGB)
    row = b"\x00" + b"\x00" * (width * 3)
    raw_data = zlib.compress(row * height)
    idat = _chunk(b"IDAT", raw_data)
    iend = _chunk(b"IEND", b"")
    png_bytes = sig + ihdr + idat + iend
    b64 = base64.b64encode(png_bytes).decode()
    return f"data:image/png;base64,{b64}"


DATA_URI = _make_png_data_uri(IMG_WIDTH, IMG_HEIGHT)
DATA_URI_300x400 = _make_png_data_uri(300, 400)
DATA_URI_800x600 = _make_png_data_uri(800, 600)
EXTERNAL_URL = "https://example.com/image.png"

# Pre-computed expected tokens for each image size
_ANTHROPIC_DATA_URI_IMAGE_TOKENS = _anthropic_image_token_count(IMG_WIDTH, IMG_HEIGHT)
_ANTHROPIC_EXTERNAL_IMAGE_TOKENS = _anthropic_image_token_count(768, 768)

# Sanity: our corrected values must differ from litellm's default
assert _ANTHROPIC_DATA_URI_IMAGE_TOKENS != _LITELLM_DEFAULT_IMAGE_TOKENS
assert _ANTHROPIC_EXTERNAL_IMAGE_TOKENS != _LITELLM_DEFAULT_IMAGE_TOKENS


def _make_message(image_urls: list[str] | None = None) -> dict:
    """Build a user message, optionally with one or more images."""
    if not image_urls:
        return {"role": "user", "content": "hello"}
    content: list[dict] = [{"type": "text", "text": "describe this image"}]
    for url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": "user", "content": content}


def _make_llm(model: str) -> DefaultLLM:
    with patch.object(DefaultLLM, "check_llm"):
        return DefaultLLM(model=model, api_key="fake-key")


# ---------- is_anthropic_model ----------

@pytest.mark.parametrize(
    "model_name, expected",
    [
        ("anthropic/claude-sonnet-4-5-20250929", True),
        ("claude-sonnet-4-5-20250929", True),
        ("vertex_ai/claude-3-5-sonnet", True),
        ("bedrock/claude-3-sonnet", True),
        ("robusta/anthropic/claude-sonnet-4-5-20250929", True),
        ("robusta/openai/gpt-4.1", False),
        ("robusta/azure/gpt-4.1", False),
        ("gpt-4.1", False),
        ("gemini-pro", False),
    ],
)
def test_is_anthropic_model(model_name: str, expected: bool):
    assert is_anthropic_model(model_name) == expected


# ---------- _get_image_dimensions ----------

@pytest.mark.parametrize(
    "url, expected_dims",
    [
        (DATA_URI, (IMG_WIDTH, IMG_HEIGHT)),
        (EXTERNAL_URL, (768, 768)),
        ("https://evil.internal/secret.png", (768, 768)),
        ("data:image/png;base64,INVALID", (768, 768)),
    ],
    ids=["data_uri", "external_url", "ssrf_url", "malformed_data_uri"],
)
def test_get_image_dimensions(url: str, expected_dims: tuple[int, int]):
    assert _get_image_dimensions(url) == expected_dims


# ---------- count_tokens: single image ----------

@pytest.mark.parametrize(
    "model, has_image, image_url, expected_image_tokens",
    [
        ("anthropic/claude-sonnet-4-5-20250929", True, DATA_URI, _ANTHROPIC_DATA_URI_IMAGE_TOKENS),
        ("anthropic/claude-sonnet-4-5-20250929", True, EXTERNAL_URL, _ANTHROPIC_EXTERNAL_IMAGE_TOKENS),
        ("anthropic/claude-sonnet-4-5-20250929", False, None, 0),
        ("gpt-4.1", True, DATA_URI, 0),
        ("gpt-4.1", False, None, 0),
        ("vertex_ai/claude-3-5-sonnet", True, DATA_URI, _ANTHROPIC_DATA_URI_IMAGE_TOKENS),
        ("robusta/anthropic/claude-sonnet-4-5-20250929", True, DATA_URI, _ANTHROPIC_DATA_URI_IMAGE_TOKENS),
        ("robusta/openai/gpt-4.1", True, DATA_URI, 0),
    ],
    ids=[
        "anthropic_data_uri",
        "anthropic_external_url",
        "anthropic_no_image",
        "openai_with_image",
        "openai_no_image",
        "vertex_claude_data_uri",
        "robusta_anthropic_data_uri",
        "robusta_openai_with_image",
    ],
)
def test_count_tokens_image_handling(
    model: str, has_image: bool, image_url: str | None, expected_image_tokens: int
):
    """Verify count_tokens applies Anthropic image correction only for Anthropic models with images."""
    urls = [image_url] if image_url else None
    message = _make_message(urls)
    messages = [message]

    text_tokens = 50
    llm = _make_llm(model)

    with patch("litellm.token_counter", return_value=text_tokens) as mock_counter:
        result = llm.count_tokens(messages)

    # Verify image blocks are stripped before passing to litellm for Anthropic
    if is_anthropic_model(model) and has_image:
        first_call_msgs = mock_counter.call_args_list[0].kwargs["messages"]
        content = first_call_msgs[0]["content"]
        assert not any(
            isinstance(b, dict) and b.get("type") == "image_url" for b in content
        ), "Image blocks should be stripped before litellm counts text"

    # Per-message token count = litellm text count + our image correction
    assert message["token_count"] == text_tokens + expected_image_tokens

    # total_tokens includes the image correction delta for Anthropic
    expected_total = text_tokens + expected_image_tokens if is_anthropic_model(model) else text_tokens
    assert result.total_tokens == expected_total


# ---------- count_tokens: multiple images of different sizes ----------

_MULTI_IMAGE_SIZES = [
    (IMG_WIDTH, IMG_HEIGHT),  # 100x200 → 26 tokens
    (300, 400),               # → 160 tokens
    (800, 600),               # → 640 tokens
]
_MULTI_IMAGE_URIS = [DATA_URI, DATA_URI_300x400, DATA_URI_800x600]
_MULTI_IMAGE_TOKENS = [_anthropic_image_token_count(w, h) for w, h in _MULTI_IMAGE_SIZES]

# Each size must produce a unique count, none matching litellm's default
assert len(set(_MULTI_IMAGE_TOKENS)) == len(_MULTI_IMAGE_TOKENS)
assert _LITELLM_DEFAULT_IMAGE_TOKENS not in _MULTI_IMAGE_TOKENS


@pytest.mark.parametrize(
    "model, expected_total_image_tokens",
    [
        ("anthropic/claude-sonnet-4-5-20250929", sum(_MULTI_IMAGE_TOKENS)),
        ("gpt-4.1", 0),
    ],
    ids=["anthropic_multi_image", "openai_multi_image"],
)
def test_count_tokens_multi_image_conversation(
    model: str, expected_total_image_tokens: int
):
    """Verify token counting across a conversation with multiple differently-sized images."""
    text_msg = _make_message()
    img_msg = _make_message(_MULTI_IMAGE_URIS)
    messages = [text_msg, img_msg]

    text_tokens = 50
    llm = _make_llm(model)

    with patch("litellm.token_counter", return_value=text_tokens):
        result = llm.count_tokens(messages)

    assert text_msg["token_count"] == text_tokens
    assert img_msg["token_count"] == text_tokens + expected_total_image_tokens

    # total_tokens = litellm bulk (on stripped msgs) + image tokens
    expected_total = text_tokens + expected_total_image_tokens if is_anthropic_model(model) else text_tokens
    assert result.total_tokens == expected_total
