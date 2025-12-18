import json

import pytest

from holmes.core.tools import ToolsetStatusEnum
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
    notion_url = (
        "https://www.notion.so/some-page-title-19dc2297bf71806d9fddc40806ae4e4d"
    )
    expected_api_url = (
        "https://api.notion.com/v1/blocks/19dc2297bf71806d9fddc40806ae4e4d/children"
    )
    assert fetch_notion_tool.convert_notion_url(notion_url) == expected_api_url

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
        ]
    }
    parsed_content = fetch_notion_tool.parse_notion_content(json.dumps(mock_response))
    expected_output = "Hello World\n\n- Bullet point"
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
