"""The `limit` parameter on fetch_datadog_logs is a default, not a ceiling.

An explicit model-requested limit must be honored on the wire (up to the
Datadog API page maximum) instead of being silently clamped down to the
configured default_limit.
"""

import json
import re

import responses

from holmes.plugins.toolsets.datadog.toolset_datadog_logs import (
    DATADOG_LOGS_API_PAGE_MAX,
    DatadogLogsToolset,
    GetLogs,
)
from tests.conftest import create_mock_tool_invoke_context

US = "https://api.datadoghq.com"
_OK_BODY = {"data": [], "meta": {"page": {"after": None}}}


def _register_search(rsps):
    # api_url is a pydantic AnyUrl (trailing slash), so the real URL has a double
    # slash before /api. Match host + path tolerant of slash count; matches both
    # the health probe and later tool calls.
    rsps.add(
        responses.POST,
        re.compile(re.escape(US) + r"/+api/v2/logs/events/search"),
        json=_OK_BODY,
        status=200,
    )


def _build(rsps):
    _register_search(rsps)
    ts = DatadogLogsToolset()
    ok, error = ts.prerequisites_callable(
        {"api_key": "k", "app_key": "a", "api_url": US, "default_limit": 100}
    )
    assert ok is True, error
    return next(t for t in ts.tools if isinstance(t, GetLogs))


def _page_limit_of_last_call(rsps) -> int:
    payload = json.loads(rsps.calls[-1].request.body)
    return payload["page"]["limit"]


class TestFetchLogsLimit:
    def test_no_limit_uses_config_default(self):
        with responses.RequestsMock() as rsps:
            tool = _build(rsps)
            tool.invoke({"query": "*"}, create_mock_tool_invoke_context())
            assert _page_limit_of_last_call(rsps) == 100

    def test_explicit_limit_above_default_is_honored(self):
        with responses.RequestsMock() as rsps:
            tool = _build(rsps)
            tool.invoke(
                {"query": "*", "limit": 500}, create_mock_tool_invoke_context()
            )
            assert _page_limit_of_last_call(rsps) == 500

    def test_limit_capped_at_datadog_api_page_max(self):
        with responses.RequestsMock() as rsps:
            tool = _build(rsps)
            tool.invoke(
                {"query": "*", "limit": 5000}, create_mock_tool_invoke_context()
            )
            assert _page_limit_of_last_call(rsps) == DATADOG_LOGS_API_PAGE_MAX
