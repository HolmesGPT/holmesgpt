"""Local MCP server that mimics a Confluence-like wiki with image attachments.

Used by eval 234 to test MCP image passthrough end-to-end without needing
external Confluence credentials. Returns real ImageContent blocks so Holmes
must use vision to read the code from the image.
"""

import base64
import os

from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("Mock Confluence Wiki")

# Load the test image at module level (before_test sets cwd to fixture dir)
_FIXTURE_DIR = os.path.dirname(os.path.abspath(__file__))
_IMAGE_PATH = os.path.join(_FIXTURE_DIR, "credentials.png")


def _load_image_bytes() -> bytes:
    with open(_IMAGE_PATH, "rb") as f:
        return f.read()


# Pre-built "database" of pages
_PAGES = {
    "page-101": {
        "id": "page-101",
        "title": "Platform Credentials",
        "space_key": "HLMS234",
        "body": (
            "This page contains the platform access credentials. "
            "The verification code is stored in the attached image file "
            "'credentials.png'. You must download and view the image to "
            "read the code."
        ),
        "attachments": [
            {
                "filename": "credentials.png",
                "media_type": "image/png",
                "comment": "Platform access code image",
            }
        ],
    },
}


@mcp.tool()
def confluence_search(query: str, limit: int = 10, spaces_filter: str = "") -> str:
    """Search for pages in the wiki. Returns matching page titles, IDs, and space keys."""
    results = []
    q_lower = query.lower()
    for page in _PAGES.values():
        if spaces_filter and page["space_key"] != spaces_filter:
            continue
        if (
            q_lower in page["title"].lower()
            or q_lower in page["body"].lower()
            or "page" in q_lower
            or page["space_key"].lower() in q_lower
        ):
            results.append(page)

    if not results:
        return "No results found."

    lines = [f"Found {len(results)} result(s):\n"]
    for p in results[:limit]:
        lines.append(
            f"- Page ID: {p['id']}, Title: \"{p['title']}\", "
            f"Space: {p['space_key']}, "
            f"Attachments: {len(p['attachments'])} file(s)"
        )
    return "\n".join(lines)


@mcp.tool()
def confluence_get_page(
    page_id: str = "",
    title: str = "",
    space_key: str = "",
    include_metadata: bool = True,
) -> str:
    """Get the content of a Confluence page by ID or by title+space_key."""
    page = None
    if page_id:
        page = _PAGES.get(page_id)
    elif title and space_key:
        for p in _PAGES.values():
            if p["title"].lower() == title.lower() and p["space_key"] == space_key:
                page = p
                break

    if not page:
        return f"Page not found. Searched with page_id='{page_id}', title='{title}', space_key='{space_key}'."

    lines = [
        f"# {page['title']}",
        f"Space: {page['space_key']}  |  Page ID: {page['id']}",
        "",
        page["body"],
        "",
    ]
    if page["attachments"]:
        lines.append(f"Attachments ({len(page['attachments'])}):")
        for att in page["attachments"]:
            lines.append(f"  - {att['filename']} ({att['media_type']}): {att['comment']}")
        lines.append("")
        lines.append(
            "Use confluence_download_attachment to download and view attachment images."
        )
    return "\n".join(lines)


@mcp.tool()
def confluence_download_attachment(page_id: str, filename: str) -> Image:
    """Download an attachment from a Confluence page. Returns the file content.
    For image attachments, returns the image directly."""
    page = _PAGES.get(page_id)
    if not page:
        raise ValueError(f"Page '{page_id}' not found.")

    att = None
    for a in page["attachments"]:
        if a["filename"].lower() == filename.lower():
            att = a
            break

    if not att:
        raise ValueError(
            f"Attachment '{filename}' not found on page '{page_id}'. "
            f"Available: {[a['filename'] for a in page['attachments']]}"
        )

    image_bytes = _load_image_bytes()
    return Image(data=image_bytes, format="png")


if __name__ == "__main__":
    mcp.run()
