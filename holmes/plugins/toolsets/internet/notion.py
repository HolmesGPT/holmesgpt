import json
import logging
import re
from typing import Any, ClassVar, Dict, Set, Tuple
from urllib.parse import urlparse

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    ToolsetTag,
)
from holmes.plugins.toolsets.internet.internet import (
    InternetBaseToolset,
    scrape,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

# Hosts we're willing to send the Notion `Authorization` header to. Anything
# else is rejected before the request is made — otherwise a caller who passes
# a non-Notion URL (and the URL-rewrite below doesn't rewrite it) would leak
# the auth token to whatever host `scrape()` ends up hitting.
NOTION_ALLOWED_HOSTS: frozenset[str] = frozenset({
    "api.notion.com",
    "www.notion.so",
    "notion.so",
})

# Notion page IDs come in two forms:
#   - dashless 32-char hex (what Notion URLs embed)
#   - 8-4-4-4-12 dashed hex UUID (what the API returns / accepts)
# Match either, strictly hex-only (no `_` or other `\w` chars), at end-of-URL.
_NOTION_ID_RE = re.compile(
    r"([0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$"
)


class FetchNotion(Tool):
    toolset: "InternetBaseToolset"

    def __init__(self, toolset: "InternetBaseToolset"):
        super().__init__(
            name="fetch_notion_webpage",
            description="Fetch a Notion webpage with HTTP requests and authentication.",
            parameters={
                "url": ToolParameter(
                    description="The URL to fetch",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,  # type: ignore
        )

    def convert_notion_url(self, url):
        if "api.notion.com" in url:
            return url
        # Strip query parameters / fragments before extracting ID
        url_without_params = url.split("?")[0].split("#")[0].rstrip("/")
        match = _NOTION_ID_RE.search(url_without_params)
        if match:
            raw_id = match.group(1).replace("-", "")
            # Format as UUID (Notion API requires dashed format)
            notion_id = f"{raw_id[:8]}-{raw_id[8:12]}-{raw_id[12:16]}-{raw_id[16:20]}-{raw_id[20:]}"
            return f"https://api.notion.com/v1/blocks/{notion_id}/children"
        return url  # Return original URL if no match is found

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        url: str = params["url"]

        # Get headers from the toolset configuration
        additional_headers = dict(
            self.toolset.internet_config.additional_headers if self.toolset.internet_config and self.toolset.internet_config.additional_headers else {}
        )
        # Notion API requires a version header
        additional_headers["Notion-Version"] = "2022-06-28"
        url = self.convert_notion_url(url)

        # Fail-closed: refuse to send the Notion Authorization header to
        # anything but a known Notion host. Without this guard a caller can
        # point the tool at an arbitrary URL (convert_notion_url passes
        # non-matching URLs through unchanged) and exfiltrate the token.
        host = (urlparse(url).hostname or "").lower()
        if host not in NOTION_ALLOWED_HOSTS:
            logging.warning(
                "Refusing Notion request to non-Notion host: %s", host or "<empty>"
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Refusing to send Notion credentials to '{host or url}'. "
                    f"Only these hosts are allowed: {sorted(NOTION_ALLOWED_HOSTS)}. "
                    "Pass a URL that points to a Notion page (e.g. "
                    "https://www.notion.so/... or https://api.notion.com/...)."
                ),
                params=params,
            )

        content, _ = scrape(url, additional_headers)

        if not content:
            logging.error("Failed to retrieve Notion content (empty response) for %s", url)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to retrieve content from {url}",
                params=params,
            )

        # scrape() returns error message strings on HTTP failures — check if content is valid JSON
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Log URL only — the body may contain authenticated page text.
            logging.error("Notion API returned non-JSON response for %s", url)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Notion API returned non-JSON response for {url}",
                params=params,
            )

        # Surface Notion's structured error responses rather than silently
        # parsing them as empty successes. Shape (per Notion API docs):
        #   {"object": "error", "status": 401, "code": "unauthorized",
        #    "message": "API token is invalid."}
        if isinstance(parsed, dict) and parsed.get("object") == "error":
            status_code = parsed.get("status")
            code = parsed.get("code")
            message = parsed.get("message")
            logging.warning(
                "Notion API error for %s: status=%s code=%s", url, status_code, code
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Notion API error for {url}: "
                    f"HTTP {status_code} {code} — {message}"
                ),
                params=params,
            )

        # Log metadata only. Response body may include authenticated page
        # text, so never log `content` or `parsed` directly.
        top_level_keys = list(parsed.keys()) if isinstance(parsed, dict) else []
        block_count = len(parsed.get("results", [])) if isinstance(parsed, dict) else 0
        logging.info(
            "Notion API response for %s: top_level_keys=%s, block_count=%d",
            url,
            top_level_keys,
            block_count,
        )

        result_data = self.parse_notion_content_from_dict(parsed)
        if not result_data:
            block_types = (
                [r.get("type") for r in parsed.get("results", [])]
                if isinstance(parsed, dict)
                else []
            )
            logging.warning(
                "Notion page parsed to empty content for %s: top_level_keys=%s, "
                "block_count=%d, block_types=%s",
                url,
                top_level_keys,
                block_count,
                block_types,
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=result_data,
            params=params,
        )

    # Block types that contain rich_text directly under their type key
    RICH_TEXT_BLOCK_TYPES: ClassVar[Set[str]] = {
        "paragraph",
        "heading_1",
        "heading_2",
        "heading_3",
        "bulleted_list_item",
        "numbered_list_item",
        "quote",
        "callout",
        "toggle",
        "to_do",
    }

    HEADING_PREFIXES: ClassVar[Dict[str, str]] = {
        "heading_1": "# ",
        "heading_2": "## ",
        "heading_3": "### ",
    }

    def parse_notion_content_from_dict(self, data: dict) -> str:
        texts = []

        for result in data.get("results", []):
            block_type = result.get("type")
            if block_type not in self.RICH_TEXT_BLOCK_TYPES:
                continue

            block_data = result.get(block_type, {})
            rich_texts = block_data.get("rich_text", [])
            formatted_text = self.format_rich_text(rich_texts)
            if not formatted_text:
                continue

            # Apply block-type-specific formatting
            if block_type in self.HEADING_PREFIXES:
                formatted_text = f"{self.HEADING_PREFIXES[block_type]}{formatted_text}"
            elif block_type == "bulleted_list_item":
                formatted_text = f"- {formatted_text}"
            elif block_type == "numbered_list_item":
                formatted_text = f"1. {formatted_text}"
            elif block_type == "quote":
                formatted_text = f"> {formatted_text}"
            elif block_type == "to_do":
                checked = block_data.get("checked", False)
                checkbox = "[x]" if checked else "[ ]"
                formatted_text = f"- {checkbox} {formatted_text}"
            elif block_type == "toggle":
                formatted_text = f"▶ {formatted_text}"

            texts.append(formatted_text)

        return "\n\n".join(texts)

    def format_rich_text(self, rich_texts: list) -> str:
        """Helper function to apply formatting (bold, code, etc.)"""
        formatted_text = []
        for text in rich_texts:
            plain_text = text["text"]["content"]
            annotations = text.get("annotations", {})

            # Apply formatting
            if annotations.get("bold"):
                plain_text = f"**{plain_text}**"
            if annotations.get("code"):
                plain_text = f"`{plain_text}`"

            formatted_text.append(plain_text)

        return "".join(formatted_text)

    def get_parameterized_one_liner(self, params) -> str:
        url: str = params["url"]
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Fetch Webpage {url}"


class NotionToolset(InternetBaseToolset):
    def __init__(self):
        super().__init__(
            name="notion",
            description="Fetch notion webpages",
            icon_url="https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/notion.svg",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/notion/",
            tools=[
                FetchNotion(self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
        )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        if not config or not config.get("additional_headers", {}):
            return (
                False,
                "Notion toolset is misconfigured. Authorization header is required.",
            )
        return super().prerequisites_callable(config)
