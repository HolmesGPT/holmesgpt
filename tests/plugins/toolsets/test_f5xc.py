"""Tests for the F5 Distributed Cloud (XC) toolset."""

import json
from unittest.mock import MagicMock

import pytest
import responses

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.f5xc.f5xc import (
    F5XCConfig,
    F5XCToolset,
    parse_embedded_json_items,
)

API_URL = "https://acmecorp.console.ves.volterra.io"


@pytest.fixture
def toolset():
    ts = F5XCToolset()
    ts.config = F5XCConfig(api_url=API_URL, api_token="test-token")
    return ts


def _tool(toolset, name):
    return next(t for t in toolset.tools if t.name == name)


class TestF5XCConfig:
    def test_minimal_config(self):
        config = F5XCConfig(api_url=API_URL, api_token="abc")
        assert config.api_url == API_URL
        assert config.api_token == "abc"
        assert config.verify_ssl is True
        assert config.timeout_seconds == 30
        assert config.default_limit == 100

    def test_missing_token_rejected(self):
        with pytest.raises(ValueError, match="api_token"):
            F5XCConfig(api_url=API_URL)

    def test_missing_url_rejected(self):
        with pytest.raises(ValueError, match="api_url"):
            F5XCConfig(api_token="abc")


class TestF5XCToolsetInit:
    def test_toolset_has_expected_tools(self, toolset):
        names = {t.name for t in toolset.tools}
        assert names == {
            "f5xc_list_namespaces",
            "f5xc_list_http_load_balancers",
            "f5xc_get_http_load_balancer",
            "f5xc_list_origin_pools",
            "f5xc_query_security_events",
            "f5xc_aggregate_security_events",
            "f5xc_query_request_logs",
        }

    def test_instructions_loaded(self, toolset):
        assert toolset.llm_instructions
        assert "ves-io-http-loadbalancer-" in toolset.llm_instructions

    def test_prerequisites_invalid_config(self, toolset):
        ok, msg = toolset.prerequisites_callable({"api_url": API_URL})
        assert ok is False
        assert "Failed to validate" in msg


class TestHealthCheck:
    def test_health_check_success(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/web/namespaces",
                json={"items": [{"name": "default"}]},
                status=200,
            )
            ok, msg = toolset.prerequisites_callable(
                {"api_url": API_URL, "api_token": "test-token"}
            )
            assert ok is True
            # The APIToken auth header must be sent
            assert (
                rsps.calls[0].request.headers["Authorization"] == "APIToken test-token"
            )

    def test_health_check_bad_token(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/web/namespaces",
                body="invalid token",
                status=401,
            )
            ok, msg = toolset.prerequisites_callable(
                {"api_url": API_URL, "api_token": "bad"}
            )
            assert ok is False
            assert "authentication failed" in msg
            assert "401" in msg

    def test_health_check_forbidden(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/web/namespaces",
                body="no access",
                status=403,
            )
            ok, msg = toolset.prerequisites_callable(
                {"api_url": API_URL, "api_token": "limited"}
            )
            assert ok is False
            assert "access denied" in msg


class TestParseEmbeddedJsonItems:
    def test_parses_json_strings(self):
        items = ['{"a": 1}', '{"b": 2}']
        assert parse_embedded_json_items(items) == [{"a": 1}, {"b": 2}]

    def test_leaves_non_json_strings(self):
        assert parse_embedded_json_items(["not json"]) == ["not json"]

    def test_leaves_objects(self):
        assert parse_embedded_json_items([{"a": 1}]) == [{"a": 1}]


class TestListNamespaces:
    def test_success(self, toolset):
        tool = _tool(toolset, "f5xc_list_namespaces")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/web/namespaces",
                json={"items": [{"name": "default", "tenant": "acmecorp"}]},
                status=200,
            )
            r = tool._invoke({}, MagicMock())
        assert r.status == StructuredToolResultStatus.SUCCESS
        assert r.data["items"][0]["name"] == "default"

    def test_http_error_includes_details(self, toolset):
        tool = _tool(toolset, "f5xc_list_namespaces")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/web/namespaces",
                body="server exploded",
                status=500,
            )
            r = tool._invoke({}, MagicMock())
        assert r.status == StructuredToolResultStatus.ERROR
        assert "500" in r.error
        assert "server exploded" in r.error
        assert "/api/web/namespaces" in r.error


class TestListHttpLoadBalancers:
    def test_success(self, toolset):
        tool = _tool(toolset, "f5xc_list_http_load_balancers")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/config/namespaces/app-ns/http_loadbalancers",
                json={"items": [{"name": "my-lb", "namespace": "app-ns"}]},
                status=200,
            )
            r = tool._invoke({"namespace": "app-ns"}, MagicMock())
        assert r.status == StructuredToolResultStatus.SUCCESS
        assert r.data["items"][0]["name"] == "my-lb"

    def test_include_spec_sends_report_fields(self, toolset):
        tool = _tool(toolset, "f5xc_list_http_load_balancers")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/config/namespaces/app-ns/http_loadbalancers",
                json={
                    "items": [
                        {
                            "name": "my-lb",
                            "get_spec": {"domains": ["app.example.com"]},
                        }
                    ]
                },
                status=200,
            )
            r = tool._invoke({"namespace": "app-ns", "include_spec": True}, MagicMock())
            assert "report_fields" in rsps.calls[0].request.url
        assert r.status == StructuredToolResultStatus.SUCCESS

    def test_no_load_balancers(self, toolset):
        tool = _tool(toolset, "f5xc_list_http_load_balancers")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/config/namespaces/empty-ns/http_loadbalancers",
                json={"items": []},
                status=200,
            )
            r = tool._invoke({"namespace": "empty-ns"}, MagicMock())
        assert r.status == StructuredToolResultStatus.NO_DATA
        assert "empty-ns" in r.data


class TestGetHttpLoadBalancer:
    def test_success(self, toolset):
        tool = _tool(toolset, "f5xc_get_http_load_balancer")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/config/namespaces/app-ns/http_loadbalancers/my-lb",
                json={
                    "metadata": {"name": "my-lb"},
                    "spec": {"domains": ["app.example.com"], "host_name": "xyz.ac.vh.ves.io"},
                },
                status=200,
            )
            r = tool._invoke({"namespace": "app-ns", "name": "my-lb"}, MagicMock())
        assert r.status == StructuredToolResultStatus.SUCCESS
        assert r.data["spec"]["domains"] == ["app.example.com"]

    def test_not_found(self, toolset):
        tool = _tool(toolset, "f5xc_get_http_load_balancer")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/config/namespaces/app-ns/http_loadbalancers/nope",
                body="object not found",
                status=404,
            )
            r = tool._invoke({"namespace": "app-ns", "name": "nope"}, MagicMock())
        assert r.status == StructuredToolResultStatus.ERROR
        assert "404" in r.error
        assert "nope" in r.error


class TestQuerySecurityEvents:
    def test_success_parses_embedded_json(self, toolset):
        tool = _tool(toolset, "f5xc_query_security_events")
        event = {
            "sec_event_type": "waf_sec_event",
            "src_ip": "1.2.3.4",
            "req_path": "/login",
            "vh_name": "ves-io-http-loadbalancer-my-lb",
        }
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/data/namespaces/app-ns/app_security/events",
                json={
                    "events": [json.dumps(event)],
                    "total_hits": "1",
                    "scroll_id": "",
                },
                status=200,
            )
            r = tool._invoke(
                {
                    "namespace": "app-ns",
                    "query": '{sec_event_type="waf_sec_event"}',
                },
                MagicMock(),
            )
            body = json.loads(rsps.calls[0].request.body)

        assert r.status == StructuredToolResultStatus.SUCCESS
        # Events are decoded from JSON-encoded strings into objects
        assert r.data["events"][0]["src_ip"] == "1.2.3.4"
        assert r.data["total_hits"] == "1"
        # Request body carries the query, namespace and RFC3339 time range
        assert body["namespace"] == "app-ns"
        assert body["query"] == '{sec_event_type="waf_sec_event"}'
        assert body["limit"] == 100
        assert "T" in body["start_time"] and "T" in body["end_time"]

    def test_limit_capped_at_api_maximum(self, toolset):
        tool = _tool(toolset, "f5xc_query_security_events")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/data/namespaces/app-ns/app_security/events",
                json={"events": ['{"a": 1}'], "total_hits": "1"},
                status=200,
            )
            tool._invoke({"namespace": "app-ns", "limit": 9999}, MagicMock())
            body = json.loads(rsps.calls[0].request.body)
        assert body["limit"] == 500

    def test_truncation_note_when_more_hits(self, toolset):
        tool = _tool(toolset, "f5xc_query_security_events")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/data/namespaces/app-ns/app_security/events",
                json={"events": ['{"a": 1}'], "total_hits": "250"},
                status=200,
            )
            r = tool._invoke({"namespace": "app-ns"}, MagicMock())
        assert "note" in r.data
        assert "250" in r.data["note"]

    def test_all_namespaces_uses_all_ns_endpoint(self, toolset):
        tool = _tool(toolset, "f5xc_query_security_events")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/data/namespaces/system/app_security/all_ns_events",
                json={"events": ['{"a": 1}'], "total_hits": "1"},
                status=200,
            )
            r = tool._invoke(
                {"namespace": "ignored", "all_namespaces": True}, MagicMock()
            )
            body = json.loads(rsps.calls[0].request.body)
        assert r.status == StructuredToolResultStatus.SUCCESS
        assert body["namespace"] == "system"

    def test_no_events(self, toolset):
        tool = _tool(toolset, "f5xc_query_security_events")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/data/namespaces/app-ns/app_security/events",
                json={"events": [], "total_hits": "0"},
                status=200,
            )
            r = tool._invoke({"namespace": "app-ns"}, MagicMock())
        assert r.status == StructuredToolResultStatus.NO_DATA
        assert "app-ns" in r.data

    def test_api_error_includes_query_details(self, toolset):
        tool = _tool(toolset, "f5xc_query_security_events")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/data/namespaces/app-ns/app_security/events",
                body="bad query syntax",
                status=400,
            )
            r = tool._invoke(
                {"namespace": "app-ns", "query": "{invalid"}, MagicMock()
            )
        assert r.status == StructuredToolResultStatus.ERROR
        assert "400" in r.error
        assert "bad query syntax" in r.error
        assert "{invalid" in r.error


class TestAggregateSecurityEvents:
    def test_success(self, toolset):
        tool = _tool(toolset, "f5xc_aggregate_security_events")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/data/namespaces/app-ns/app_security/events/aggregation",
                json={
                    "aggs": {
                        "fieldAggregation_SRC_IP_10": {
                            "field_aggregation": {
                                "buckets": [{"key": "1.2.3.4", "count": "42"}]
                            }
                        }
                    }
                },
                status=200,
            )
            r = tool._invoke(
                {"namespace": "app-ns", "field": "src_ip"}, MagicMock()
            )
            body = json.loads(rsps.calls[0].request.body)

        assert r.status == StructuredToolResultStatus.SUCCESS
        assert r.data["buckets"] == [{"key": "1.2.3.4", "count": "42"}]
        # Field is uppercased in the request
        assert body["aggs"]["fieldAggregation_SRC_IP_10"]["field_aggregation"] == {
            "field": "SRC_IP",
            "topk": 10,
        }

    def test_no_buckets(self, toolset):
        tool = _tool(toolset, "f5xc_aggregate_security_events")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/data/namespaces/app-ns/app_security/events/aggregation",
                json={"aggs": {}},
                status=200,
            )
            r = tool._invoke(
                {"namespace": "app-ns", "field": "SRC_IP"}, MagicMock()
            )
        assert r.status == StructuredToolResultStatus.NO_DATA


class TestQueryRequestLogs:
    def test_success_uses_logs_key(self, toolset):
        tool = _tool(toolset, "f5xc_query_request_logs")
        log = {
            "method": "GET",
            "req_path": "/checkout",
            "rsp_code": "503",
            "src_ip": "9.8.7.6",
        }
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/data/namespaces/app-ns/access_logs",
                json={"logs": [json.dumps(log)], "total_hits": "1", "scroll_id": ""},
                status=200,
            )
            r = tool._invoke(
                {
                    "namespace": "app-ns",
                    "query": '{rsp_code_class=~"4xx|5xx"}',
                },
                MagicMock(),
            )
        assert r.status == StructuredToolResultStatus.SUCCESS
        assert r.data["logs"][0]["rsp_code"] == "503"

    def test_no_logs(self, toolset):
        tool = _tool(toolset, "f5xc_query_request_logs")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/data/namespaces/app-ns/access_logs",
                json={"logs": [], "total_hits": "0"},
                status=200,
            )
            r = tool._invoke({"namespace": "app-ns"}, MagicMock())
        assert r.status == StructuredToolResultStatus.NO_DATA
        assert "ves-io-http-loadbalancer-" in r.data
