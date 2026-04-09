"""Local MCP server that returns many images to fill the context window.

Used by eval 238 to test that compaction handles multimodal (image) content
without crashing. Each call to get_gallery_page returns a batch of images,
and the LLM is asked to fetch multiple pages. The combined image tokens
should trigger compaction. The key test: compaction strips images before
summarizing, so the compaction LLM call doesn't overflow.
"""

import base64
import os
import struct
import zlib

from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("Image Gallery Service")

# Gallery has 3 pages of 4 images each = 12 images total.
# Each image is 400x300 PNG (~1600 Anthropic tokens per image).
# 12 images × ~1600 tokens = ~19,200 image tokens — enough to push
# a small context window over the compaction threshold.
_NUM_PAGES = 3
_IMAGES_PER_PAGE = 4
_IMG_WIDTH = 400
_IMG_HEIGHT = 300

# Verification code embedded in text — the LLM must report this AFTER compaction
_VERIFICATION_CODE = "GALLERY-COMPACT-8K2M"


def _make_png(width: int, height: int, seed: int = 0) -> bytes:
    """Create a minimal valid PNG with deterministic content based on seed."""
    # IHDR chunk
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    # IDAT chunk — use seed to make each image slightly different
    row = b"\x00" + bytes([(seed + i) % 256 for i in range(width * 3)])
    raw = zlib.compress(row * height)
    idat_crc = zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF
    idat = struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", idat_crc)
    # IEND chunk
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


# Pre-generate all images at import time
_IMAGES = [_make_png(_IMG_WIDTH, _IMG_HEIGHT, seed=i) for i in range(_NUM_PAGES * _IMAGES_PER_PAGE)]


@mcp.tool()
def list_gallery_info() -> str:
    """Get information about the image gallery.

    Returns the gallery structure and verification code.
    """
    return (
        f"Gallery contains {_NUM_PAGES} pages with {_IMAGES_PER_PAGE} images each.\n"
        f"Total images: {_NUM_PAGES * _IMAGES_PER_PAGE}\n"
        f"Pages available: 1 through {_NUM_PAGES}\n"
        f"Verification code: {_VERIFICATION_CODE}\n"
        f"Fetch each page with get_gallery_page(page_number=N) to view all images."
    )


@mcp.tool()
def get_gallery_page(page_number: int) -> list:
    """Fetch a page of images from the gallery.

    Each page contains multiple images. Returns the images for visual inspection.
    """
    if page_number < 1 or page_number > _NUM_PAGES:
        return [f"Invalid page number {page_number}. Valid pages: 1-{_NUM_PAGES}"]

    start_idx = (page_number - 1) * _IMAGES_PER_PAGE
    result: list = [
        f"Gallery page {page_number}/{_NUM_PAGES} - showing images {start_idx + 1} to {start_idx + _IMAGES_PER_PAGE}. "
        f"Each image is {_IMG_WIDTH}x{_IMG_HEIGHT}px."
    ]
    for i in range(_IMAGES_PER_PAGE):
        result.append(Image(data=_IMAGES[start_idx + i], format="png"))

    return result


if __name__ == "__main__":
    mcp.run()
