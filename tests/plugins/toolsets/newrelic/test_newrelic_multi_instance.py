"""Multi-instance proof for New Relic via the wrapper.

New Relic is single-instance on master (its `enable_multi_account` is a separate
axis). `multi_instance(NewRelicToolset)` makes it multi-instance. Each instance has
its own API key + account; a routed NRQL query uses the selected instance's
Api-Key header and account id on the wire.
"""

import re

import pytest
import responses

from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from holmes.plugins.toolsets.newrelic.newrelic import NewRelicToolset
from tests.conftest import create_mock_tool_invoke_context

GRAPHQL = "https://api.newrelic.com/graphql"
_NRQL_OK = {"data": {"actor": {"account": {"nrql": {"results": [{"count": 1}]}}}}}


def _mock(rsps):
    rsps.add(responses.POST, re.compile(re.escape(GRAPHQL)), json=_NRQL_OK, status=200)


class TestNewRelicFlat:
    def test_flat_config_backwards_compatible(self):
        ts = multi_instance(NewRelicToolset)
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            _mock(rsps)
            ok, _ = ts.prerequisites_callable({"api_key": "NRAK-1", "account_id": "111"})
        assert ok is True
        assert list(ts._children) == ["default"]
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)
        assert all(INSTANCE_PARAM_NAME not in t.parameters for t in ts.tools)


class TestNewRelicMultiInstance:
    def _build(self, rsps):
        _mock(rsps)
        ts = multi_instance(NewRelicToolset)
        ok, _ = ts.prerequisites_callable(
            {
                "instances": [
                    {"name": "team-a", "api_key": "NRAK-a", "account_id": "111"},
                    {"name": "team-b", "api_key": "NRAK-b", "account_id": "222"},
                ]
            }
        )
        assert ok is True
        return ts

    def test_tool_surface(self):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
        assert any(t.name == "newrelic_list_instances" for t in ts.tools)
        for tool in ts.tools:
            if not isinstance(tool, ListInstancesTool):
                assert INSTANCE_PARAM_NAME in tool.parameters

    @pytest.mark.parametrize(
        "instance,api_key,account",
        [("team-a", "NRAK-a", "111"), ("team-b", "NRAK-b", "222")],
    )
    def test_each_instance_uses_its_own_key_and_account(self, instance, api_key, account):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
            tool = next(t for t in ts.tools if t.name == "newrelic_execute_nrql_query")
            # _build() ran per-instance prerequisite health-checks; make sure the
            # routed invoke() actually emits its own request so we don't assert
            # against a leftover prerequisite call (false green).
            before = len(rsps.calls)
            tool.invoke(
                {
                    "query": "SELECT count(*) FROM Transaction",
                    "description": "count transactions test",
                    "query_type": "Other",
                    INSTANCE_PARAM_NAME: instance,
                },
                create_mock_tool_invoke_context(),
            )
            assert len(rsps.calls) == before + 1
            last = rsps.calls[-1].request
            assert last.headers.get("Api-Key") == api_key
            assert f"account(id: {account})" in last.body.decode()
