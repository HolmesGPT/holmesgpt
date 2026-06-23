"""Multi-instance proof for Elasticsearch via the NEW wrapper.

The previously hand-rolled multi-instance support was reverted; Elasticsearch is
now a plain single-instance toolset made multi-instance by `multi_instance(...)`.
Verifies flat backwards-compat, routing surface, and per-instance wire calls.
"""

import re

import pytest
import responses

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
    ElasticsearchClusterToolset,
    ElasticsearchDataToolset,
)
from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from tests.conftest import create_mock_tool_invoke_context

EU = "https://es-eu.internal:9200"
US = "https://es-us.internal:9200"


def _health(rsps, url):
    rsps.add(
        responses.GET,
        re.compile(re.escape(url) + r"/_cluster/health.*"),
        json={"cluster_name": "c", "status": "green"},
        status=200,
    )


class TestElasticsearchFlat:
    def test_flat_config_backwards_compatible(self):
        ts = multi_instance(ElasticsearchClusterToolset)
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            _health(rsps, EU)
            ok, _ = ts.prerequisites_callable({"api_url": EU, "api_key": "k"})
        assert ok is True
        assert list(ts._children) == ["default"]
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)
        assert all(INSTANCE_PARAM_NAME not in t.parameters for t in ts.tools)


class TestElasticsearchMultiInstance:
    def _build_cluster(self, rsps):
        _health(rsps, EU)
        _health(rsps, US)
        ts = multi_instance(ElasticsearchClusterToolset)
        ok, _ = ts.prerequisites_callable(
            {
                "instances": [
                    {"name": "eu", "api_url": EU, "api_key": "k_eu"},
                    {"name": "us", "api_url": US, "api_key": "k_us"},
                ]
            }
        )
        assert ok is True
        return ts

    def test_cluster_tool_surface(self):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build_cluster(rsps)
        assert any(t.name == "elasticsearch_cluster_list_instances" for t in ts.tools)
        for tool in ts.tools:
            if not isinstance(tool, ListInstancesTool):
                assert INSTANCE_PARAM_NAME in tool.parameters

    def test_data_toolset_has_its_own_scoped_list_tool(self):
        # Distinct list-tool name per ES toolset so they don't collide.
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            _health(rsps, EU)
            _health(rsps, US)
            ts = multi_instance(ElasticsearchDataToolset)
            ts.prerequisites_callable(
                {"instances": [
                    {"name": "eu", "api_url": EU, "api_key": "k_eu"},
                    {"name": "us", "api_url": US, "api_key": "k_us"},
                ]}
            )
        assert any(t.name == "elasticsearch_data_list_instances" for t in ts.tools)

    @pytest.mark.parametrize(
        "instance,host,key",
        [("eu", EU, "k_eu"), ("us", US, "k_us")],
    )
    def test_each_instance_calls_its_own_cluster(self, instance, host, key):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build_cluster(rsps)
            tool = next(t for t in ts.tools if t.name == "elasticsearch_cluster_health")
            result = tool.invoke(
                {INSTANCE_PARAM_NAME: instance}, create_mock_tool_invoke_context()
            )
            assert result.status is StructuredToolResultStatus.SUCCESS
            last = rsps.calls[-1].request
            assert last.url.startswith(f"{host}/_cluster/health")
            assert last.headers.get("Authorization") == f"ApiKey {key}"
