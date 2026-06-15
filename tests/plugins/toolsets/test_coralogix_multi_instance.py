"""Multi-instance proof for Coralogix through the actual wrapper.

Coralogix is unchanged from master; `multi_instance(CoralogixToolset)` makes it
multi-instance. It has no `api_url` (region via `domain`) and Bearer auth. HTTP
is mocked; routed calls are asserted on the wire.
"""

import re

import pytest
import responses

from holmes.plugins.toolsets.coralogix.toolset_coralogix import CoralogixToolset
from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from tests.conftest import create_mock_tool_invoke_context

EU = "eu2.coralogix.com"
US = "cx498.coralogix.com"


def _query_url(domain):
    return f"https://ng-api-http.{domain}/api/v1/dataprime/query"


def _mock_query(rsps, domain):
    # One registration matches both the health probe and routed calls.
    rsps.add(
        responses.POST,
        re.compile(re.escape(_query_url(domain))),
        json={"result": {"results": []}},
        status=200,
    )


class TestCoralogixFlat:
    def test_flat_config_backwards_compatible(self):
        ts = multi_instance(CoralogixToolset)
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            _mock_query(rsps, EU)
            ok, _ = ts.prerequisites_callable({"domain": EU, "api_key": "k"})
        assert ok is True
        assert list(ts._children) == ["default"]
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)
        assert all(INSTANCE_PARAM_NAME not in t.parameters for t in ts.tools)


class TestCoralogixMultiInstance:
    def _build(self, rsps):
        _mock_query(rsps, EU)
        _mock_query(rsps, US)
        ts = multi_instance(CoralogixToolset)
        ok, _ = ts.prerequisites_callable(
            {
                "instances": [
                    {"name": "eu", "domain": EU, "api_key": "k_eu"},
                    {"name": "us", "domain": US, "api_key": "k_us"},
                ]
            }
        )
        assert ok is True
        return ts

    def test_tool_surface_and_list_instances(self):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
        assert any(t.name == "coralogix_list_instances" for t in ts.tools)
        list_tool = next(t for t in ts.tools if isinstance(t, ListInstancesTool))
        got = {
            i["name"]: i.get("domain")
            for i in list_tool._invoke({}, create_mock_tool_invoke_context()).data[
                "instances"
            ]
        }
        assert got == {"eu": EU, "us": US}
        for tool in ts.tools:
            if not isinstance(tool, ListInstancesTool):
                assert INSTANCE_PARAM_NAME in tool.parameters

    @pytest.mark.parametrize(
        "instance,domain,key",
        [("eu", EU, "k_eu"), ("us", US, "k_us")],
    )
    def test_each_instance_calls_its_own_endpoint(self, instance, domain, key):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
            tool = next(
                t for t in ts.tools if t.name == "coralogix_execute_dataprime_query"
            )
            tool.invoke(
                {
                    "query": "source logs | limit 1",
                    "description": "fetch one log line",
                    "query_type": "Logs",
                    "start_date": "2026-01-01T00:00:00Z",
                    "end_date": "2026-01-01T01:00:00Z",
                    INSTANCE_PARAM_NAME: instance,
                },
                create_mock_tool_invoke_context(),
            )
            last = rsps.calls[-1].request
            assert last.url.startswith(f"https://ng-api-http.{domain}/")
            assert last.headers.get("Authorization") == f"Bearer {key}"
