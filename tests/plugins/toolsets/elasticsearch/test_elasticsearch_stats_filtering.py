"""Tests for narrowing large Elasticsearch stats responses.

Regression tests for a customer issue: on clusters with many/big indexes,
`<pattern>/_stats` returns a multi-MB per-index payload that exceeds the
single-tool token budget, and elasticsearch_index_stats offered no way to
narrow the response (no level, no filter_path, no jq) — so Holmes could not
answer aggregate capacity questions. See eval
tests/llm/fixtures/test_ask_holmes/283_elasticsearch_index_stats_aggregate.
"""

import re

import pytest
import responses

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
    ElasticsearchClusterToolset,
)
from tests.conftest import create_mock_tool_invoke_context

ES_URL = "https://es.internal:9200"

STATS_RESPONSE = {
    "_shards": {"total": 1800, "successful": 1800, "failed": 0},
    "_all": {
        "primaries": {
            "docs": {"count": 23397, "deleted": 0},
            "store": {"size_in_bytes": 13097834},
        },
        "total": {
            "docs": {"count": 23397, "deleted": 0},
            "store": {"size_in_bytes": 13097834},
        },
    },
    "indices": {
        "app-283-platform-api-2024.02.01": {
            "total": {"docs": {"count": 5}, "store": {"size_in_bytes": 7276}}
        },
        "app-283-platform-api-2024.02.02": {
            "total": {"docs": {"count": 8}, "store": {"size_in_bytes": 7301}}
        },
    },
}

NODES_STATS_RESPONSE = {
    "_nodes": {"total": 2, "successful": 2, "failed": 0},
    "cluster_name": "test-cluster",
    "nodes": {
        "node-1": {"jvm": {"mem": {"heap_used_percent": 42}}},
        "node-2": {"jvm": {"mem": {"heap_used_percent": 57}}},
    },
}


@pytest.fixture
def cluster_toolset():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(
            responses.GET,
            re.compile(re.escape(ES_URL) + r"/_cluster/health.*"),
            json={"cluster_name": "test-cluster", "status": "green"},
            status=200,
        )
        toolset = ElasticsearchClusterToolset()
        ok, msg = toolset.prerequisites_callable({"api_url": ES_URL, "api_key": "k"})
        assert ok, msg
    return toolset


def get_tool(toolset, name):
    tool = next((t for t in toolset.tools if t.name == name), None)
    assert tool is not None, f"tool {name} not found"
    return tool


class TestIndexStatsNarrowing:
    def test_level_is_passed_as_query_param(self, cluster_toolset):
        tool = get_tool(cluster_toolset, "elasticsearch_index_stats")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{ES_URL}/app-283-*/_stats",
                match=[
                    responses.matchers.query_param_matcher(
                        {"level": "cluster"}, strict_match=False
                    )
                ],
                json=STATS_RESPONSE,
                status=200,
            )
            result = tool.invoke(
                {"index": "app-283-*", "level": "cluster"},
                create_mock_tool_invoke_context(),
            )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["_all"]["total"]["docs"]["count"] == 23397

    def test_filter_path_is_passed_as_query_param(self, cluster_toolset):
        tool = get_tool(cluster_toolset, "elasticsearch_index_stats")
        filtered = {"_all": {"total": {"docs": {"count": 23397}}}}
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{ES_URL}/app-283-*/_stats",
                match=[
                    responses.matchers.query_param_matcher(
                        {"filter_path": "_all.total.docs"}, strict_match=False
                    )
                ],
                json=filtered,
                status=200,
            )
            result = tool.invoke(
                {"index": "app-283-*", "filter_path": "_all.total.docs"},
                create_mock_tool_invoke_context(),
            )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == filtered

    def test_jq_filters_response_client_side(self, cluster_toolset):
        tool = get_tool(cluster_toolset, "elasticsearch_index_stats")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{ES_URL}/app-283-*/_stats",
                json=STATS_RESPONSE,
                status=200,
            )
            result = tool.invoke(
                {"index": "app-283-*", "jq": "._all.total.docs.count"},
                create_mock_tool_invoke_context(),
            )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == 23397

    def test_metrics_still_appended_to_path(self, cluster_toolset):
        tool = get_tool(cluster_toolset, "elasticsearch_index_stats")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{ES_URL}/app-283-*/_stats/docs,store",
                json=STATS_RESPONSE,
                status=200,
            )
            result = tool.invoke(
                {"index": "app-283-*", "metrics": "docs,store"},
                create_mock_tool_invoke_context(),
            )
        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_narrowing_parameters_are_exposed(self, cluster_toolset):
        tool = get_tool(cluster_toolset, "elasticsearch_index_stats")
        for param in ("level", "filter_path", "jq", "max_depth"):
            assert param in tool.parameters, f"missing parameter: {param}"


class TestNodesStatsNarrowing:
    def test_filter_path_is_passed_as_query_param(self, cluster_toolset):
        tool = get_tool(cluster_toolset, "elasticsearch_nodes_stats")
        filtered = {"nodes": {"node-1": {"jvm": {"mem": {"heap_used_percent": 42}}}}}
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{ES_URL}/_nodes/_all/stats",
                match=[
                    responses.matchers.query_param_matcher(
                        {"filter_path": "nodes.*.jvm.mem"}, strict_match=False
                    )
                ],
                json=filtered,
                status=200,
            )
            result = tool.invoke(
                {"filter_path": "nodes.*.jvm.mem"},
                create_mock_tool_invoke_context(),
            )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == filtered

    def test_jq_filters_response_client_side(self, cluster_toolset):
        tool = get_tool(cluster_toolset, "elasticsearch_nodes_stats")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{ES_URL}/_nodes/_all/stats",
                json=NODES_STATS_RESPONSE,
                status=200,
            )
            result = tool.invoke(
                {"jq": ".nodes | map_values(.jvm.mem.heap_used_percent)"},
                create_mock_tool_invoke_context(),
            )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == {"node-1": 42, "node-2": 57}

    def test_narrowing_parameters_are_exposed(self, cluster_toolset):
        tool = get_tool(cluster_toolset, "elasticsearch_nodes_stats")
        for param in ("filter_path", "jq", "max_depth"):
            assert param in tool.parameters, f"missing parameter: {param}"
