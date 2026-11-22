from typing import Any, Dict, Optional

import pytest

from holmes.core.tools import StructuredToolResultStatus, ToolsetStatusEnum
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.toolsets.internet import ssrf
from holmes.plugins.toolsets.internet.internet import InternetToolset
from holmes.plugins.toolsets.internet.notion import NotionToolset
from tests.conftest import create_mock_tool_invoke_context

PUBLIC_URL = "https://docs.vendor.test/page"
PAGE = "hello from the page"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(
        ssrf.socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", port))],
    )


def assert_refused(result, responses):
    # scrape() hands its refusal back as the tool's data, so the status alone
    # does not tell a refusal from a fetched page.
    refused_in_data = "Refusing to fetch" in (result.data or "")
    assert result.status == StructuredToolResultStatus.ERROR or refused_in_data
    assert PAGE not in (result.data or "")
    assert len(responses.calls) == 0


def fetch_tool(config: Optional[Dict[str, Any]] = None, prepare: bool = True):
    toolset = InternetToolset()
    if prepare:
        success, error = toolset.prerequisites_callable(config or {})
        assert success, error
    toolset.status = ToolsetStatusEnum.ENABLED
    tool = ToolExecutor(toolsets=[toolset]).get_tool_by_name("fetch_webpage")
    assert tool
    return tool


def invoke(tool, url: str = PUBLIC_URL, user_approved: bool = False):
    return tool.invoke(
        {"url": url}, create_mock_tool_invoke_context(user_approved=user_approved)
    )


def test_fetch_requires_approval_by_default(responses):
    result = invoke(fetch_tool())

    assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED
    assert "docs.vendor.test" in result.error
    assert "allowed_hosts" in result.error
    assert len(responses.calls) == 0


def test_fetch_requires_approval_when_prerequisites_never_ran(responses):
    result = invoke(fetch_tool(prepare=False))

    assert result.status == StructuredToolResultStatus.APPROVAL_REQUIRED
    assert len(responses.calls) == 0


def test_fetch_runs_after_user_approval(responses):
    responses.get(PUBLIC_URL, status=200, body=PAGE)

    result = invoke(fetch_tool(), user_approved=True)

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == PAGE


def test_approved_fetch_still_blocks_internal_addresses(responses):
    result = invoke(
        fetch_tool(), url="http://169.254.169.254/latest/meta-data/", user_approved=True
    )

    assert_refused(result, responses)


@pytest.mark.parametrize(
    "url", ["https://docs.vendor.test/page", "https://docs.docs.vendor.test/page"]
)
def test_allowed_host_and_subdomain_need_no_approval(responses, url):
    responses.get(url, status=200, body=PAGE)

    result = invoke(fetch_tool({"allowed_hosts": ["docs.vendor.test"]}), url=url)

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == PAGE


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.attacker.test/x?leak=secret",
        "https://docs.vendor.test.evil.attacker.test/",
        "https://docs.vendor.test@evil.attacker.test/",
    ],
)
def test_host_outside_allowlist_is_refused_without_approval_prompt(responses, url):
    tool = fetch_tool({"allowed_hosts": ["docs.vendor.test"]})

    assert tool.requires_approval({"url": url}, create_mock_tool_invoke_context()) is None
    result = invoke(tool, url=url)

    assert_refused(result, responses)


def test_require_approval_false_restores_open_fetch(responses):
    responses.get(PUBLIC_URL, status=200, body=PAGE)

    result = invoke(fetch_tool({"require_approval": False}))

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == PAGE


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.com/x", "not a url", "", "https://"]
)
def test_unfetchable_url_is_rejected_without_approval_prompt(responses, url):
    result = invoke(fetch_tool(), url=url)

    assert_refused(result, responses)


def test_missing_url_is_rejected_without_approval_prompt(responses):
    result = fetch_tool().invoke({}, create_mock_tool_invoke_context())

    assert_refused(result, responses)


def test_approval_reason_names_host_not_credentials():
    approval = fetch_tool().requires_approval(
        {"url": "https://user:hunter2@docs.vendor.test/page"},
        create_mock_tool_invoke_context(),
    )

    assert approval and approval.needs_approval
    assert "docs.vendor.test" in approval.reason
    assert "hunter2" not in approval.reason


def test_notion_fetch_is_not_gated():
    toolset = NotionToolset()
    assert toolset.prerequisites_callable(
        {"additional_headers": {"Authorization": "Bearer x"}}
    )[0]
    tool = toolset.tools[0]

    approval = tool.requires_approval(
        {"url": "https://www.notion.so/page"}, create_mock_tool_invoke_context()
    )

    assert approval is None


def test_malformed_url_asks_no_approval():
    approval = fetch_tool().requires_approval(
        {"url": "http://[not-an-ipv6"}, create_mock_tool_invoke_context()
    )

    assert approval is None
