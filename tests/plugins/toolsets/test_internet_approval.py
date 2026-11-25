from typing import Any, Dict, Optional

import pytest

from holmes.core.tools import StructuredToolResultStatus, ToolsetStatusEnum
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.toolsets.internet import ssrf
from holmes.plugins.toolsets.internet.internet import InternetToolset
from holmes.plugins.toolsets.internet.notion import NotionToolset
from tests.conftest import create_mock_tool_invoke_context

DOCS_URL = "https://docs.vendor.test/page"
OTHER_URL = "https://other.site.test/page"
PAGE = "hello from the page"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(
        ssrf.socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", port))],
    )


def fetch_tool(config: Optional[Dict[str, Any]] = None, prepare: bool = True):
    toolset = InternetToolset()
    if prepare:
        success, error = toolset.prerequisites_callable(config or {})
        assert success, error
    toolset.status = ToolsetStatusEnum.ENABLED
    tool = ToolExecutor(toolsets=[toolset]).get_tool_by_name("fetch_webpage")
    assert tool
    return tool


def invoke(tool, url: str = DOCS_URL, user_approved: bool = False):
    return tool.invoke(
        {"url": url}, create_mock_tool_invoke_context(user_approved=user_approved)
    )


def asks_approval(tool, url: str = DOCS_URL) -> bool:
    approval = tool.requires_approval({"url": url}, create_mock_tool_invoke_context())
    return bool(approval and approval.needs_approval)


def assert_not_fetched(result, responses, requests_made: int = 0):
    # scrape() hands a refusal back as the tool's data, so the status alone
    # does not tell a refusal from a fetched page.
    refused_in_data = "Refusing to" in (result.data or "")
    assert result.status == StructuredToolResultStatus.ERROR or refused_in_data
    assert PAGE not in (result.data or "")
    assert len(responses.calls) == requests_made


def test_default_config_asks_before_fetch(responses):
    result = invoke(fetch_tool())

    assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED
    assert "docs.vendor.test" in result.error
    assert "allowed_hosts" in result.error
    assert len(responses.calls) == 0


def test_unprepared_toolset_asks_before_fetch(responses):
    result = invoke(fetch_tool(prepare=False))

    assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED
    assert len(responses.calls) == 0


def test_fetch_after_approval(responses):
    responses.get(DOCS_URL, status=200, body=PAGE)

    result = invoke(fetch_tool(), user_approved=True)

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == PAGE


def test_address_checks_apply_after_approval(responses):
    result = invoke(fetch_tool(), url="http://127.0.0.1/status", user_approved=True)

    assert_not_fetched(result, responses)


@pytest.mark.parametrize(
    "url", ["https://docs.vendor.test/page", "https://api.docs.vendor.test/page"]
)
def test_listed_host_and_subdomain_fetch_directly(responses, url):
    responses.get(url, status=200, body=PAGE)
    tool = fetch_tool({"allowed_hosts": ["docs.vendor.test"]})

    assert not asks_approval(tool, url)
    result = invoke(tool, url=url)

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == PAGE


@pytest.mark.parametrize(
    "url",
    [
        OTHER_URL,
        "https://docs.vendor.test.other.site.test/",
        "https://docs.vendor.test@other.site.test/",
    ],
)
def test_host_matching_with_a_host_list(responses, url):
    tool = fetch_tool({"allowed_hosts": ["docs.vendor.test"]})

    assert not asks_approval(tool, url)
    assert_not_fetched(invoke(tool, url=url), responses)


@pytest.mark.parametrize("hosts", [[""], ["  "], ["", " "]])
def test_blank_host_entries_count_as_no_host_list(responses, hosts):
    tool = fetch_tool({"allowed_hosts": hosts})

    assert asks_approval(tool)
    assert invoke(tool).status == StructuredToolResultStatus.APPROVAL_REQUIRED
    assert len(responses.calls) == 0


def test_host_entries_are_trimmed(responses):
    responses.get(DOCS_URL, status=200, body=PAGE)
    tool = fetch_tool({"allowed_hosts": ["", " docs.vendor.test "]})

    assert not asks_approval(tool)
    assert invoke(tool).data == PAGE


def test_require_approval_false_fetches_directly(responses):
    responses.get(DOCS_URL, status=200, body=PAGE)

    result = invoke(fetch_tool({"require_approval": False}))

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == PAGE


@pytest.mark.parametrize(
    "url",
    ["file:///tmp/page", "ftp://docs.vendor.test/x", "not a url", "", "https://"],
)
def test_unsupported_url_returns_error_without_prompt(responses, url):
    tool = fetch_tool()

    assert not asks_approval(tool, url)
    assert_not_fetched(invoke(tool, url=url), responses)


def test_missing_url_returns_error_without_prompt(responses):
    result = fetch_tool().invoke({}, create_mock_tool_invoke_context())

    assert result.status == StructuredToolResultStatus.ERROR
    assert len(responses.calls) == 0


def test_malformed_url_returns_error_without_prompt(responses):
    tool = fetch_tool()
    url = "http://[not-an-address"

    assert not asks_approval(tool, url)
    assert_not_fetched(invoke(tool, url=url, user_approved=True), responses)


def test_approval_reason_shows_host_only():
    approval = fetch_tool().requires_approval(
        {"url": "https://name:value@docs.vendor.test/page"},
        create_mock_tool_invoke_context(),
    )

    assert approval and approval.needs_approval
    assert "docs.vendor.test" in approval.reason
    assert "value" not in approval.reason


def test_notion_tool_has_no_prompt():
    toolset = NotionToolset()
    assert toolset.prerequisites_callable(
        {"additional_headers": {"Authorization": "Bearer x"}}
    )[0]

    approval = toolset.tools[0].requires_approval(
        {"url": "https://www.notion.so/page"}, create_mock_tool_invoke_context()
    )

    assert approval is None


def redirect(responses, location):
    responses.get(DOCS_URL, status=302, headers={"Location": location})


def test_redirect_to_other_host_after_approval(responses):
    redirect(responses, OTHER_URL)

    result = invoke(fetch_tool(), user_approved=True)

    assert "Refusing to follow the redirect" in result.data
    assert OTHER_URL in result.data
    assert [call.request.url for call in responses.calls] == [DOCS_URL]


@pytest.mark.parametrize(
    "location",
    ["/moved", "https://docs.vendor.test/moved", "https://www.docs.vendor.test/moved"],
)
def test_redirect_on_same_site_after_approval(responses, location):
    redirect(responses, location)
    target = location if location.startswith("http") else "https://docs.vendor.test/moved"
    responses.get(target, status=200, body=PAGE)

    result = invoke(fetch_tool(), user_approved=True)

    assert result.data == PAGE


@pytest.mark.parametrize(
    "config",
    [
        {"require_approval": False},
        {"allowed_hosts": ["docs.vendor.test", "other.site.test"]},
    ],
)
def test_redirect_to_other_host_without_prompt_config(responses, config):
    redirect(responses, OTHER_URL)
    responses.get(OTHER_URL, status=200, body=PAGE)

    result = invoke(fetch_tool(config))

    assert result.data == PAGE


def test_redirect_to_unlisted_host_with_a_host_list(responses):
    redirect(responses, OTHER_URL)

    result = invoke(fetch_tool({"allowed_hosts": ["docs.vendor.test"]}))

    assert_not_fetched(result, responses, requests_made=1)
