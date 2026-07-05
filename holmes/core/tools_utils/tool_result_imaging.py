"""Text-to-image compression for large tool results.

Inspired by pxpipe (https://github.com/teamchong/pxpipe): Claude bills image
tokens by pixel area (roughly width*height/750) regardless of how much text
the image contains, while dense machine output (kubectl tables, logs, JSON)
often costs 3+ text tokens per 10 characters. Rendering a large tool result
as a tightly packed monospace PNG can therefore carry the same characters for
fewer tokens.

This is an experimental, opt-in feature. It is lossy for byte-exact values
(long hashes/IDs may be misread by the vision encoder), and per pxpipe's own
benchmarks Opus-family models misread imaged content far more often than
Fable. Enable with HOLMES_TOOL_RESULT_IMAGING=true.

Environment variables:
    HOLMES_TOOL_RESULT_IMAGING            enable the feature (default: false)
    HOLMES_TOOL_RESULT_IMAGING_MIN_CHARS  only image results larger than this
                                          (default: 3000)
    HOLMES_TOOL_RESULT_IMAGING_MAX_PAGES  keep result as text if it would need
                                          more pages than this (default: 6)
    HOLMES_TOOL_RESULT_IMAGING_FONT_SIZE  monospace font size in px
                                          (default: 13)
"""

import base64
import io
import logging
import math
import os
from typing import List, Optional, Tuple

from holmes.common.env_vars import load_bool

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # Pillow is optional; imaging degrades to plain text
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment,misc]

try:
    from litellm import token_counter
except ImportError:
    token_counter = None  # type: ignore[assignment]

# Anthropic resizes images above ~1.15 megapixels (~1600 image tokens at
# width*height/750). Staying below that avoids a lossy server-side downscale
# that would blur the text.
PAGE_WIDTH = 1072
PAGE_MAX_HEIGHT = 1072
MARGIN = 8
# Fallback estimate of text tokens per character when no tokenizer is
# available. Dense machine output (kubectl tables, logs, JSON) measures at
# ~2.0 chars/token; 3.0 is deliberately conservative so the gate errs toward
# keeping text.
TEXT_CHARS_PER_TOKEN_ESTIMATE = 3.0
# Only image the result if the estimated image tokens are below this fraction
# of the estimated text tokens.
PROFITABILITY_RATIO = 0.75

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",  # some RH-based images
    "/System/Library/Fonts/Menlo.ttc",  # macOS
]

_font_cache: dict = {}


def imaging_enabled() -> bool:
    return bool(load_bool("HOLMES_TOOL_RESULT_IMAGING", False))


def imaging_min_chars() -> int:
    # Below ~3000 chars the fixed page-area cost usually beats text anyway and
    # the profitability gate rejects it; this floor just avoids pointless
    # rendering work on small results.
    return int(os.environ.get("HOLMES_TOOL_RESULT_IMAGING_MIN_CHARS", "3000"))


def _imaging_max_pages() -> int:
    return int(os.environ.get("HOLMES_TOOL_RESULT_IMAGING_MAX_PAGES", "6"))


DEFAULT_FONT_SIZE = 13


def _imaging_font_size() -> int:
    size = int(
        os.environ.get("HOLMES_TOOL_RESULT_IMAGING_FONT_SIZE", str(DEFAULT_FONT_SIZE))
    )
    if size < 1 or size > PAGE_MAX_HEIGHT // 4:
        logging.warning(
            "Invalid HOLMES_TOOL_RESULT_IMAGING_FONT_SIZE=%d, using default %d",
            size,
            DEFAULT_FONT_SIZE,
        )
        return DEFAULT_FONT_SIZE
    return size


def _load_font(size: int):
    if ImageFont is None:
        return None
    if size in _font_cache:
        return _font_cache[size]
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            font = ImageFont.truetype(path, size)
            _font_cache[size] = font
            return font
    return None


def _wrap_lines(text: str, max_cols: int) -> List[str]:
    """Hard-wrap each line at max_cols characters, preserving original layout."""
    wrapped: List[str] = []
    for line in text.splitlines() or [""]:
        # Tabs render at unpredictable widths in fixed columns; normalize them.
        line = line.replace("\t", "    ")
        if not line:
            wrapped.append("")
            continue
        while len(line) > max_cols:
            wrapped.append(line[:max_cols])
            line = line[max_cols:]
        wrapped.append(line)
    return wrapped


def render_text_to_images(
    text: str,
    font_size: Optional[int] = None,
) -> Optional[Tuple[List[dict], int]]:
    """Render text into one or more dense monospace PNG pages.

    Returns (images, estimated_image_tokens) where images is a list of
    {"mimeType": "image/png", "data": <base64>} dicts in reading order,
    or None if rendering is not possible (Pillow or fonts unavailable).
    """
    if Image is None or ImageDraw is None:
        logging.warning(
            "HOLMES_TOOL_RESULT_IMAGING is enabled but Pillow is not installed"
        )
        return None

    font_size = font_size or _imaging_font_size()
    font = _load_font(font_size)
    if font is None:
        logging.warning(
            "HOLMES_TOOL_RESULT_IMAGING is enabled but no monospace font was found"
        )
        return None

    char_width = font.getlength("M")
    line_height = font_size + 2
    # Clamp to >=1 so a pathological font size can't zero these out (max_cols=0
    # would make _wrap_lines loop forever; lines_per_page=0 breaks pagination)
    max_cols = max(1, int((PAGE_WIDTH - 2 * MARGIN) // char_width))
    lines_per_page = max(1, (PAGE_MAX_HEIGHT - 2 * MARGIN) // line_height)

    lines = _wrap_lines(text, max_cols)
    pages = [
        lines[i : i + lines_per_page] for i in range(0, len(lines), lines_per_page)
    ]

    images: List[dict] = []
    estimated_tokens = 0
    for page_lines in pages:
        height = min(PAGE_MAX_HEIGHT, len(page_lines) * line_height + 2 * MARGIN)
        img = Image.new("L", (PAGE_WIDTH, height), color=255)
        draw = ImageDraw.Draw(img)
        y = MARGIN
        for line in page_lines:
            if line:
                draw.text((MARGIN, y), line, font=font, fill=0)
            y += line_height
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        images.append(
            {
                "mimeType": "image/png",
                "data": base64.b64encode(buf.getvalue()).decode("ascii"),
            }
        )
        estimated_tokens += math.ceil(PAGE_WIDTH * height / 750)

    return images, estimated_tokens


def _estimate_text_tokens(text: str) -> float:
    """Estimate how many text tokens this string would cost.

    Uses litellm's tokenizer (fast, tiktoken-based) as an approximation of the
    provider tokenizer; falls back to a chars-per-token heuristic.
    """
    if token_counter is None:
        return len(text) / TEXT_CHARS_PER_TOKEN_ESTIMATE
    try:
        return token_counter(model="gpt-4o", text=text)
    except Exception:
        return len(text) / TEXT_CHARS_PER_TOKEN_ESTIMATE


def maybe_image_tool_output(text: str) -> Optional[List[dict]]:
    """Return image pages for a tool output if imaging is enabled and profitable.

    Returns None when the output should stay as plain text: feature disabled,
    output too small, too many pages, or images would not save enough tokens.
    """
    if not imaging_enabled():
        return None
    if len(text) < imaging_min_chars():
        return None

    rendered = render_text_to_images(text)
    if rendered is None:
        return None
    images, image_tokens = rendered

    if len(images) > _imaging_max_pages():
        logging.debug(
            "Tool result imaging skipped: %d pages exceeds max %d",
            len(images),
            _imaging_max_pages(),
        )
        return None

    estimated_text_tokens = _estimate_text_tokens(text)
    if image_tokens > estimated_text_tokens * PROFITABILITY_RATIO:
        logging.debug(
            "Tool result imaging skipped: ~%d image tokens vs ~%d text tokens",
            image_tokens,
            int(estimated_text_tokens),
        )
        return None

    logging.info(
        "Tool result imaging: %d chars -> %d page(s), ~%d image tokens "
        "(vs ~%d estimated text tokens)",
        len(text),
        len(images),
        image_tokens,
        int(estimated_text_tokens),
    )
    return images
