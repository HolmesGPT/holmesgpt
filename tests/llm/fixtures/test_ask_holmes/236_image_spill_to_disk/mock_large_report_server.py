"""Local MCP server that returns a large report with an embedded image.

Used by eval 236 to test the image spill-to-disk pipeline end-to-end.
The tool returns a large text payload + an image, which exceeds the per-tool
context window limit. Holmes must use read_image_file to recover the image
from disk and read the verification code.
"""

import os

from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("Large Report Service")

_FIXTURE_DIR = os.path.dirname(os.path.abspath(__file__))
_IMAGE_PATH = os.path.join(_FIXTURE_DIR, "credentials.png")


def _load_image_bytes() -> bytes:
    with open(_IMAGE_PATH, "rb") as f:
        return f.read()


# Generate a large filler text (~100K chars ≈ ~25K tokens) to force spill-to-disk
_FILLER_BLOCK = (
    "This section contains detailed diagnostic information for the platform audit. "
    "All subsystems were checked and the results are summarized below. "
    "Please review each section carefully for any anomalies. "
    "The monitoring data was collected over a 24-hour window. "
    "No critical issues were detected in the automated scan.\n"
)
_LARGE_REPORT = _FILLER_BLOCK * 500  # ~100K chars


@mcp.tool()
def list_reports(category: str = "") -> str:
    """List available diagnostic reports. Returns report IDs and titles."""
    return (
        "Available reports:\n"
        "- report-2024-001: Platform Audit Q4 (category: security, has_image: true)\n"
        "  Contains verification image with access code.\n"
    )


@mcp.tool()
def get_report(report_id: str) -> list:
    """Fetch a diagnostic report by ID. Returns report content including any attached images.

    The report may contain images that need visual inspection.
    """
    if report_id != "report-2024-001":
        return [f"Report '{report_id}' not found. Use list_reports to see available reports."]

    image_bytes = _load_image_bytes()

    # Return both text and image — the text is large enough to trigger spill-to-disk
    report_text = (
        "# Platform Audit Report Q4 - report-2024-001\n\n"
        "## Summary\n"
        "Full platform security audit completed. An image containing the "
        "verification access code is attached below. You must visually inspect "
        "the image to read the code.\n\n"
        "## Detailed Findings\n\n"
        + _LARGE_REPORT
        + "\n## Verification\n"
        "The attached image contains the verification code for this report. "
        "Inspect the image to retrieve it.\n"
    )

    return [report_text, Image(data=image_bytes, format="png")]


if __name__ == "__main__":
    mcp.run()
