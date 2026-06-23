"""Multi-instance proof for Grafana (dashboards/loki/tempo) via the NEW wrapper.

The previously hand-rolled Grafana dashboard multi-instance support was reverted;
all three Grafana toolsets are now plain single-instance toolsets made
multi-instance by `multi_instance(...)`. Verifies flat backwards-compat, routing
surface, and per-instance wire calls (each instance → its own host + Bearer key).
"""

import re

import pytest
import responses

from holmes.plugins.toolsets.grafana.loki.toolset_grafana_loki import GrafanaLokiToolset
from holmes.plugins.toolsets.grafana.toolset_grafana import GrafanaToolset
from holmes.plugins.toolsets.grafana.toolset_grafana_tempo import GrafanaTempoToolset
from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from tests.conftest import create_mock_tool_invoke_context


def _reg(rsps, method, url_regex, body):
    rsps.add(method, re.compile(url_regex), json=body, status=200)


class TestGrafanaDashboards:
    EU = "https://graf-eu.example.com"
    US = "https://graf-us.example.com"

    def _mock(self, rsps):
        # health (/api/dashboards/tags) + search (/api/search) on both hosts
        _reg(rsps, responses.GET, r"https://graf-(eu|us)\.example\.com/api/(dashboards/tags|search).*", [])

    def _build(self, rsps):
        self._mock(rsps)
        ts = multi_instance(GrafanaToolset)
        ok, _ = ts.prerequisites_callable(
            {"instances": [
                {"name": "eu", "api_url": self.EU, "api_key": "k_eu"},
                {"name": "us", "api_url": self.US, "api_key": "k_us"},
            ]}
        )
        assert ok is True
        return ts

    def test_flat_backwards_compatible(self):
        ts = multi_instance(GrafanaToolset)
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            self._mock(rsps)
            ok, _ = ts.prerequisites_callable({"api_url": self.EU, "api_key": "k"})
        assert ok is True
        assert list(ts._children) == ["default"]
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)
        assert all(INSTANCE_PARAM_NAME not in t.parameters for t in ts.tools)

    def test_tool_surface(self):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
        assert any(t.name == "grafana_dashboards_list_instances" for t in ts.tools)
        for tool in ts.tools:
            if not isinstance(tool, ListInstancesTool):
                assert INSTANCE_PARAM_NAME in tool.parameters

    @pytest.mark.parametrize("instance,host,key", [("eu", EU, "k_eu"), ("us", US, "k_us")])
    def test_each_instance_calls_its_own_grafana(self, instance, host, key):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
            tool = next(t for t in ts.tools if t.name == "grafana_search_dashboards")
            tool.invoke({"query": "x", INSTANCE_PARAM_NAME: instance}, create_mock_tool_invoke_context())
            last = rsps.calls[-1].request
            assert last.url.startswith(f"{host}/api/search")
            assert last.headers.get("Authorization") == f"Bearer {key}"


class TestGrafanaLoki:
    EU = "http://loki-eu.svc:3100"
    US = "http://loki-us.svc:3100"

    def _mock(self, rsps):
        _reg(rsps, responses.GET, r"http://loki-(eu|us)\.svc:3100/loki/api/v1/.*", {"data": {"result": []}})
        _reg(rsps, responses.POST, r"http://loki-(eu|us)\.svc:3100/loki/api/v1/.*", {"data": {"result": []}})

    def _build(self, rsps):
        self._mock(rsps)
        ts = multi_instance(GrafanaLokiToolset)
        ok, _ = ts.prerequisites_callable(
            {"instances": [
                {"name": "eu", "api_url": self.EU, "api_key": "k_eu"},
                {"name": "us", "api_url": self.US, "api_key": "k_us"},
            ]}
        )
        assert ok is True
        return ts

    def test_tool_surface(self):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
        assert any(t.name == "grafana_loki_list_instances" for t in ts.tools)

    @pytest.mark.parametrize("instance,host,key", [("eu", EU, "k_eu"), ("us", US, "k_us")])
    def test_each_instance_calls_its_own_loki(self, instance, host, key):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
            tool = next(t for t in ts.tools if t.name == "grafana_loki_query")
            tool.invoke(
                {"query": '{job="x"}', INSTANCE_PARAM_NAME: instance},
                create_mock_tool_invoke_context(),
            )
            hosts = [c.request.url for c in rsps.calls if c.request.url.startswith(f"{host}/loki")]
            assert hosts, f"no call to {host}"
            assert rsps.calls[-1].request.headers.get("Authorization") == f"Bearer {key}"


class TestGrafanaBasicAuth:
    """Basic auth (username/password) restored from master.

    The Grafana toolset authenticates via `api_key` (Bearer) OR `username`+`password`
    (HTTP basic auth). Verifies a top-level username/password falls through to every
    instance and is sent as `Authorization: Basic ...` on the wire, while a per-instance
    api_key still wins for the instance that sets it.
    """

    import base64

    EU = "https://gba-eu.example.com"
    US = "https://gba-us.example.com"

    def _mock(self, rsps):
        _reg(rsps, responses.GET, r"https://gba-(eu|us)\.example\.com/api/(dashboards/tags|search).*", [])

    def _build(self, rsps):
        self._mock(rsps)
        ts = multi_instance(GrafanaToolset)
        ok, _ = ts.prerequisites_callable(
            {
                "username": "admin",
                "password": "prom-operator",
                "instances": [
                    {"name": "eu", "api_url": self.EU},
                    {"name": "us", "api_url": self.US, "api_key": "tok_us"},
                ],
            }
        )
        assert ok is True
        return ts

    def test_global_basic_auth_falls_through(self):
        expected = "Basic " + self.base64.b64encode(b"admin:prom-operator").decode()
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
            tool = next(t for t in ts.tools if t.name == "grafana_search_dashboards")
            tool.invoke({"query": "x", INSTANCE_PARAM_NAME: "eu"}, create_mock_tool_invoke_context())
            last = rsps.calls[-1].request
            assert last.headers.get("Authorization") == expected

    def test_per_instance_api_key_overrides_global_basic_auth(self):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
            tool = next(t for t in ts.tools if t.name == "grafana_search_dashboards")
            tool.invoke({"query": "x", INSTANCE_PARAM_NAME: "us"}, create_mock_tool_invoke_context())
            last = rsps.calls[-1].request
            # instance set its own api_key -> Bearer, NOT inherited basic auth
            assert last.headers.get("Authorization") == "Bearer tok_us"


class TestGrafanaTempo:
    EU = "http://tempo-eu.svc:3200"
    US = "http://tempo-us.svc:3200"

    def _mock(self, rsps):
        _reg(rsps, responses.GET, r"http://tempo-(eu|us)\.svc:3200/api/.*", {"traces": []})

    def _build(self, rsps):
        self._mock(rsps)
        ts = multi_instance(GrafanaTempoToolset)
        ok, _ = ts.prerequisites_callable(
            {"instances": [
                {"name": "eu", "api_url": self.EU, "api_key": "k_eu"},
                {"name": "us", "api_url": self.US, "api_key": "k_us"},
            ]}
        )
        assert ok is True
        return ts

    def test_tool_surface(self):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
        assert any(t.name == "grafana_tempo_list_instances" for t in ts.tools)

    @pytest.mark.parametrize("instance,host,key", [("eu", EU, "k_eu"), ("us", US, "k_us")])
    def test_each_instance_calls_its_own_tempo(self, instance, host, key):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
            tool = next(t for t in ts.tools if t.name == "tempo_search_traces_by_query")
            tool.invoke(
                {"q": '{ .service.name = "x" }', INSTANCE_PARAM_NAME: instance},
                create_mock_tool_invoke_context(),
            )
            calls = [c.request for c in rsps.calls if c.request.url.startswith(f"{host}/api")]
            assert calls, f"no call to {host}"
            assert calls[-1].headers.get("Authorization") == f"Bearer {key}"
