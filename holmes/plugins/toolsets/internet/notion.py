import re
import logging
import json
from typing import Any, Dict, Tuple
from holmes.core.tools import (
    Tool,
    ToolInvokeContext,
    ToolParameter,
    ToolsetTag,
)
from holmes.plugins.toolsets.internet.internet import (
    InternetBaseToolset,
    scrape,
)
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


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
        match = re.search(r"-(\w{32})$", url)
        if match:
            notion_id = match.group(1)
            return f"https://api.notion.com/v1/blocks/{notion_id}/children"
        return url  # Return original URL if no match is found

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        url: str = params["url"]

        # Get headers from the toolset configuration
        additional_headers = (
            self.toolset.additional_headers if self.toolset.additional_headers else {}
        )
        url = self.convert_notion_url(url)
        content, _ = scrape(url, additional_headers)

        if not content:
            logging.error(f"Failed to retrieve content from {url}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to retrieve content from {url}",
                params=params,
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=self.parse_notion_content(content),
            params=params,
        )

    def parse_notion_content(self, content: Any) -> str:
        data = json.loads(content)
        texts = []

        for result in data.get("results", []):
            # Handle paragraph blocks
            if result.get("type") == "paragraph":
                rich_texts = result["paragraph"].get("rich_text", [])
                formatted_text = self.format_rich_text(rich_texts)
                if formatted_text:
                    texts.append(formatted_text)

            # Handle bulleted list items
            elif result.get("type") == "bulleted_list_item":
                rich_texts = result["bulleted_list_item"].get("rich_text", [])
                formatted_text = self.format_rich_text(rich_texts)
                if formatted_text:
                    texts.append(f"- {formatted_text}")

        # Join and return the formatted text
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
            icon_url="https://upload.wikimedia.org/wikipedia/commons/thumb/e/e9/Notion-logo.svg/2048px-Notion-logo.svg.png",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/notion/",
            tools=[
                FetchNotion(self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
            is_default=False,
        )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        if not config or not config.get("additional_headers", {}):
            return (
                False,
                "Notion toolset is misconfigured. Authorization header is required.",
            )
        self.additional_headers = config["additional_headers"]
        return True, ""
