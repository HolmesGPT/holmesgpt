import json
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.tools import StructuredToolResultStatus, ToolsetStatusEnum
from holmes.plugins.toolsets.internet.notion import FetchNotion, NotionToolset

notion_config = {
    "additional_headers": {
        "Authorization": "Bearer fake_token",
        "Notion-Version": "2022-06-28",
    },
}


@pytest.fixture(scope="module", autouse=True)
def notion_toolset():
    toolset = NotionToolset()
    toolset.config = notion_config
    toolset.status = ToolsetStatusEnum.ENABLED
    toolset.check_prerequisites()
    assert (
        toolset.status == ToolsetStatusEnum.ENABLED
    ), "Prerequisites check failed for Notion toolset"
    return toolset


@pytest.fixture(scope="module")
def fetch_notion_tool(notion_toolset):
    return FetchNotion(notion_toolset)


def test_convert_notion_url(fetch_notion_tool):
    # Notion API requires page IDs in UUID format (with dashes)
    notion_url = (
        "https://www.notion.so/some-page-title-19dc2297bf71806d9fddc40806ae4e4d"
    )
    expected_api_url = (
        "https://api.notion.com/v1/blocks/19dc2297-bf71-806d-9fdd-c40806ae4e4d/children"
    )
    assert fetch_notion_tool.convert_notion_url(notion_url) == expected_api_url

    # URL with query parameters should still be parsed correctly
    notion_url_with_params = f"{notion_url}?source=copy_link"
    assert fetch_notion_tool.convert_notion_url(notion_url_with_params) == expected_api_url

    api_url = "https://api.notion.com/v1/blocks/1234/children"
    assert (
        fetch_notion_tool.convert_notion_url(api_url) == api_url
    )  # Should return unchanged


def test_parse_notion_content(fetch_notion_tool):
    mock_response = {
        "results": [
            {
                "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": "Hello World"}}]},
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"text": {"content": "Bullet point"}}]
                },
            },
            {
                "type": "heading_1",
                "heading_1": {"rich_text": [{"text": {"content": "A Heading"}}]},
            },
            {
                "type": "to_do",
                "to_do": {
                    "rich_text": [{"text": {"content": "A task"}}],
                    "checked": False,
                },
            },
        ]
    }
    parsed_content = fetch_notion_tool.parse_notion_content_from_dict(mock_response)
    expected_output = "Hello World\n\n- Bullet point\n\n# A Heading\n\n- [ ] A task"
    assert parsed_content == expected_output


def test_format_rich_text(fetch_notion_tool):
    rich_text_input = [
        {"text": {"content": "Bold"}, "annotations": {"bold": True}},
        {"text": {"content": " Normal "}},
        {"text": {"content": "Code"}, "annotations": {"code": True}},
    ]
    formatted_text = fetch_notion_tool.format_rich_text(rich_text_input)
    expected_output = "**Bold** Normal `Code`"
    assert formatted_text == expected_output


def test_tool_one_liner(fetch_notion_tool):
    url = "https://www.notion.so/fake-url"
    assert (
        fetch_notion_tool.get_parameterized_one_liner({"url": url})
        == f"Notion: Fetch Webpage {url}"
    )


def test_convert_notion_url_dashed_uuid(fetch_notion_tool):
    """Notion URLs sometimes carry the dashed 8-4-4-4-12 UUID form."""
    url = "https://www.notion.so/19dc2297-bf71-806d-9fdd-c40806ae4e4d"
    expected = (
        "https://api.notion.com/v1/blocks/19dc2297-bf71-806d-9fdd-c40806ae4e4d/children"
    )
    assert fetch_notion_tool.convert_notion_url(url) == expected


def test_convert_notion_url_rejects_non_hex(fetch_notion_tool):
    """`\\w{32}` would have matched `_` and other non-hex chars — hex-only must not."""
    bogus = "https://www.notion.so/page-id_with_underscores_1234567890abcdef12"
    # The trailing 32-char substring contains `_`, so it should NOT match.
    # Fall through to "return url unchanged".
    assert fetch_notion_tool.convert_notion_url(bogus) == bogus


def test_invoke_rejects_non_notion_host(fetch_notion_tool):
    """SSRF guard: never send Notion auth header to arbitrary hosts."""
    with patch(
        "holmes.plugins.toolsets.internet.notion.scrape"
    ) as mock_scrape:
        result = fetch_notion_tool._invoke(
            {"url": "https://attacker.example.com/steal"},
            context=MagicMock(),
        )
    assert result.status == StructuredToolResultStatus.ERROR
    assert "attacker.example.com" in result.error
    assert "Refusing" in result.error or "not allowed" in result.error.lower() or "allowed" in result.error.lower()
    # Critical: scrape() must NOT have been called — no header leak possible.
    mock_scrape.assert_not_called()


def test_invoke_surfaces_notion_api_error(fetch_notion_tool):
    """Notion returns JSON error shape — must surface, not silently succeed."""
    error_body = json.dumps(
        {
            "object": "error",
            "status": 401,
            "code": "unauthorized",
            "message": "API token is invalid.",
        }
    )
    with patch(
        "holmes.plugins.toolsets.internet.notion.scrape",
        return_value=(error_body, None),
    ):
        result = fetch_notion_tool._invoke(
            {
                "url": "https://api.notion.com/v1/blocks/19dc2297-bf71-806d-9fdd-c40806ae4e4d/children"
            },
            context=MagicMock(),
        )
    assert result.status == StructuredToolResultStatus.ERROR
    assert "401" in result.error
    assert "unauthorized" in result.error
    assert "API token is invalid" in result.error
