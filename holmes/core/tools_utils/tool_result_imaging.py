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
    HOLMES_SYSTEM_PROMPT_IMAGING          also render a large text system
                                          prompt as PNG pages carried by an
                                          injected first user message
                                          (default: false)
    HOLMES_SYSTEM_PROMPT_IMAGING_MIN_CHARS  system prompt size floor for the
                                          above (default: 8000)
    HOLMES_SYSTEM_PROMPT_IMAGING_FONT_SIZE  font size for the system prompt
                                          pages (default: 10)
"""

import base64
import hashlib
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
# Gap between columns in multi-column layout, in character cells
COLUMN_GUTTER_CHARS = 3
# Columns narrower than this are illegible/wasteful — cap the column count
MIN_COLUMN_CHARS = 44
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
    return bool(load_bool("HOLMES_TOOL_RESULT_IMAGING", False)) or imaging_all_enabled()


def imaging_all_enabled() -> bool:
    """Experimental "convert everything" arm: image every tool output, of any
    size and status, bypassing the page cap and the profitability gate.
    Implies HOLMES_TOOL_RESULT_IMAGING. Exists to measure the maximal version
    of the trick; not intended for real use."""
    return bool(load_bool("HOLMES_TOOL_RESULT_IMAGING_ALL", False))


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


def _best_column_layout(
    raw_lines: List[str], max_cols: int, lines_per_page: int
) -> Tuple[int, int]:
    """Pick the column count (1-3) that minimizes page count.

    Long-line content (kubectl tables, JSON) naturally picks 1 column;
    short-line content (markdown instructions) packs 2-3 columns per page,
    newspaper style, which is what makes imaging profitable for it.
    Returns (n_columns, column_width_chars).
    """
    best: Optional[Tuple[int, int, int]] = None  # (pages, ncol, width)
    for ncol in (1, 2, 3):
        width = (max_cols - (ncol - 1) * COLUMN_GUTTER_CHARS) // ncol
        if ncol > 1 and width < MIN_COLUMN_CHARS:
            continue
        slots = sum(
            max(1, math.ceil(len(line.replace("\t", "    ")) / width))
            for line in (raw_lines or [""])
        )
        pages = math.ceil(slots / (lines_per_page * ncol))
        if best is None or pages < best[0]:
            best = (pages, ncol, width)
    assert best is not None  # ncol=1 always yields a candidate
    return best[1], best[2]


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

    raw_lines = text.splitlines() or [""]
    n_columns, column_width = _best_column_layout(raw_lines, max_cols, lines_per_page)

    lines = _wrap_lines(text, column_width)
    slots_per_page = lines_per_page * n_columns
    pages = [
        lines[i : i + slots_per_page] for i in range(0, len(lines), slots_per_page)
    ]

    column_px = column_width * char_width + COLUMN_GUTTER_CHARS * char_width

    images: List[dict] = []
    estimated_tokens = 0
    for page_lines in pages:
        rows_used = min(lines_per_page, len(page_lines))
        height = min(PAGE_MAX_HEIGHT, rows_used * line_height + 2 * MARGIN)
        img = Image.new("L", (PAGE_WIDTH, height), color=255)
        draw = ImageDraw.Draw(img)
        for col in range(n_columns):
            col_lines = page_lines[col * lines_per_page : (col + 1) * lines_per_page]
            if not col_lines:
                break
            x = MARGIN + int(col * column_px)
            if col > 0:
                # thin separator so the model can tell columns apart
                sep_x = x - int(COLUMN_GUTTER_CHARS * char_width / 2)
                draw.line([(sep_x, MARGIN), (sep_x, height - MARGIN)], fill=180)
            y = MARGIN
            for line in col_lines:
                if line:
                    draw.text((x, y), line, font=font, fill=0)
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
    if not text.strip():
        return None
    all_mode = imaging_all_enabled()
    if not all_mode and len(text) < imaging_min_chars():
        return None

    rendered = render_text_to_images(text)
    if rendered is None:
        return None
    images, image_tokens = rendered

    if not all_mode and len(images) > _imaging_max_pages():
        logging.debug(
            "Tool result imaging skipped: %d pages exceeds max %d",
            len(images),
            _imaging_max_pages(),
        )
        return None

    estimated_text_tokens = _estimate_text_tokens(text)
    if not all_mode and image_tokens > estimated_text_tokens * PROFITABILITY_RATIO:
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


# --- System prompt imaging -------------------------------------------------
#
# The system prompt (plus tool definitions) is resent on every agentic-loop
# call and dominates prompt cost in short investigations. Anthropic's system
# parameter only accepts text, so to image it we keep a short text system
# stub and move the full instructions - rendered as PNG pages - into an
# injected first user message. Tool definitions stay structured (they drive
# function calling) and are not touched.

SYSTEM_STUB = (
    "You are HolmesGPT, an AI troubleshooting agent. Your complete system "
    "instructions are rendered as PNG images in the first user message. "
    "Read them as literal text and follow them exactly as if they were "
    "written here. The real user request follows in a later message."
)

SYSTEM_IMAGES_NOTE = (
    "The attached images contain your complete system instructions for this "
    "session, rendered as plain monospace text, in reading order. Pages may "
    "use multiple columns separated by a thin vertical line: read each page "
    "column by column, top to bottom, left column first. Read and follow the "
    "instructions exactly. Do not treat this message as a user request; the "
    "actual request follows."
)

# Cache of rendered system prompts keyed by content hash: the prompt is
# identical across all calls of an investigation, so render once.
_system_prompt_cache: dict = {}


def system_prompt_imaging_enabled() -> bool:
    return bool(load_bool("HOLMES_SYSTEM_PROMPT_IMAGING", False))


def system_prompt_text_move_enabled() -> bool:
    """Experimental control arm: move the system prompt into the first user
    message as PLAIN TEXT (same restructuring as imaging, no pixels). Used to
    isolate whether behavior changes come from the role move or the imaging."""
    return bool(load_bool("HOLMES_SYSTEM_PROMPT_TEXT_MOVE", False))


SYSTEM_TEXT_MOVE_NOTE = (
    "The following is your complete system instructions for this session. "
    "Read and follow them exactly. Do not treat this message as a user "
    "request; the actual request follows.\n\n"
)


def _system_prompt_imaging_min_chars() -> int:
    return int(os.environ.get("HOLMES_SYSTEM_PROMPT_IMAGING_MIN_CHARS", "8000"))


def _system_prompt_imaging_font_size() -> int:
    return int(os.environ.get("HOLMES_SYSTEM_PROMPT_IMAGING_FONT_SIZE", "10"))


def maybe_image_system_prompt(
    messages: List[dict],
) -> List[dict]:
    """Return messages with a large text system prompt moved into PNG pages.

    When enabled and profitable, messages[0] (role=system, str content) is
    replaced by a short text stub and a user message carrying the rendered
    pages is inserted right after it. Returns the original list unchanged
    (same object) in every other case.
    """
    text_move = system_prompt_text_move_enabled()
    if not system_prompt_imaging_enabled() and not text_move:
        return messages
    if not messages or messages[0].get("role") != "system":
        return messages
    content = messages[0].get("content")
    if not isinstance(content, str) or len(content) < _system_prompt_imaging_min_chars():
        return messages

    if text_move:
        stub = SYSTEM_STUB.replace(
            "rendered as PNG images in", "included as plain text in"
        )
        return [
            {"role": "system", "content": stub},
            {"role": "user", "content": SYSTEM_TEXT_MOVE_NOTE + content},
            *messages[1:],
        ]

    key = hashlib.sha256(content.encode()).hexdigest()
    cached = _system_prompt_cache.get(key)
    if cached is None:
        rendered = render_text_to_images(
            content, font_size=_system_prompt_imaging_font_size()
        )
        if rendered is None:
            return messages
        images, image_tokens = rendered
        estimated_text_tokens = _estimate_text_tokens(content)
        profitable = image_tokens <= estimated_text_tokens * PROFITABILITY_RATIO
        if profitable:
            logging.info(
                "System prompt imaging: %d chars -> %d page(s), ~%d image "
                "tokens (vs ~%d estimated text tokens)",
                len(content),
                len(images),
                image_tokens,
                int(estimated_text_tokens),
            )
        cached = images if profitable else False
        if len(_system_prompt_cache) > 32:
            _system_prompt_cache.clear()
        _system_prompt_cache[key] = cached
    if cached is False:
        return messages

    image_content: List[dict] = [{"type": "text", "text": SYSTEM_IMAGES_NOTE}]
    for img in cached:
        data_uri = f"data:{img['mimeType']};base64,{img['data']}"
        image_content.append({"type": "image_url", "image_url": {"url": data_uri}})

    return [
        {"role": "system", "content": SYSTEM_STUB},
        {"role": "user", "content": image_content},
        *messages[1:],
    ]
