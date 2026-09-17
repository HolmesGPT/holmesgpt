"""Config-driven request timeout for the New Relic toolset.

`timeout_seconds` on the toolset config must reach the actual `requests.post`
call made by NewRelicAPI, both for the prerequisite health check and for tool
invocations.
"""

import re

import pytest
import responses
from pydantic import ValidationError

from holmes.plugins.toolsets.newrelic.newrelic import NewrelicConfig, NewRelicToolset
from tests.conftest import create_mock_tool_invoke_context

GRAPHQL = "https://api.newrelic.com/graphql"
_NRQL_OK = {"data": {"actor": {"account": {"nrql": {"results": [{"count": 1}]}}}}}


def _mock(rsps):
    rsps.add(responses.POST, re.compile(re.escape(GRAPHQL)), json=_NRQL_OK, status=200)


def test_default_timeout_used_on_requests():
    ts = NewRelicToolset()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        _mock(rsps)
        ok, _ = ts.prerequisites_callable({"api_key": "NRAK-1", "account_id": "111"})
        assert ok is True
        assert ts.timeout_seconds == 30
        assert rsps.calls[-1].request.req_kwargs["timeout"] == 30


def test_configured_timeout_used_on_requests():
    ts = NewRelicToolset()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        _mock(rsps)
        ok, _ = ts.prerequisites_callable(
            {"api_key": "NRAK-1", "account_id": "111", "timeout_seconds": 120}
        )
        assert ok is True
        assert ts.timeout_seconds == 120
        # Prerequisite health-check request already uses the configured timeout
        assert rsps.calls[-1].request.req_kwargs["timeout"] == 120

        # And so does a routed tool invocation
        tool = next(t for t in ts.tools if t.name == "newrelic_execute_nrql_query")
        before = len(rsps.calls)
        tool.invoke(
            {
                "query": "SELECT count(*) FROM Transaction",
                "description": "count transactions test",
                "query_type": "Other",
            },
            create_mock_tool_invoke_context(),
        )
        assert len(rsps.calls) == before + 1
        assert rsps.calls[-1].request.req_kwargs["timeout"] == 120


def test_non_positive_timeout_rejected():
    with pytest.raises(ValidationError):
        NewrelicConfig(api_key="NRAK-1", account_id="111", timeout_seconds=0)
