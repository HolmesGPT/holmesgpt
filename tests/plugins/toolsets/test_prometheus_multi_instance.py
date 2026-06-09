"""Multi-instance proof for Prometheus through the actual wrapper.

Prometheus is unchanged from master; `multi_instance(PrometheusToolset)` makes it
multi-instance. Instances differ by `prometheus_url` + `additional_headers`. HTTP
is mocked; the routed call is asserted on the wire (host + per-instance headers).
"""

import re

import pytest
import responses

from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from holmes.plugins.toolsets.prometheus.prometheus import PrometheusToolset
from tests.conftest import create_mock_tool_invoke_context

A = "http://prom-a.svc:9090/"
B = "http://prom-b.svc:9090/"


def _mock_prom(rsps):
    # Health (/api/v1/query?query=up) + tool calls (/api/v1/rules etc.) on both hosts.
    rsps.add(
        responses.GET,
        re.compile(r"http://prom-[ab]\.svc:9090/api/v1/.*"),
        json={"status": "success", "data": {"groups": [], "result": []}},
        status=200,
    )
    rsps.add(
        responses.POST,
        re.compile(r"http://prom-[ab]\.svc:9090/api/v1/.*"),
        json={"status": "success", "data": {"result": []}},
        status=200,
    )


class TestPrometheusFlat:
    def test_flat_config_backwards_compatible(self):
        ts = multi_instance(PrometheusToolset)
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            _mock_prom(rsps)
            ok, _ = ts.prerequisites_callable({"prometheus_url": A})
        assert ok is True
        assert list(ts._children) == ["default"]
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)
        assert all(INSTANCE_PARAM_NAME not in t.parameters for t in ts.tools)


class TestPrometheusMultiInstance:
    def _build(self, rsps):
        _mock_prom(rsps)
        ts = multi_instance(PrometheusToolset)
        ok, _ = ts.prerequisites_callable(
            {
                "instances": [
                    {"name": "a", "prometheus_url": A,
                     "additional_headers": {"X-Scope-OrgID": "team-a"}},
                    {"name": "b", "prometheus_url": B,
                     "additional_headers": {"X-Scope-OrgID": "team-b"}},
                ]
            }
        )
        assert ok is True
        return ts

    def test_tool_surface(self):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
        assert any(t.name == "prometheus_metrics_list_instances" for t in ts.tools)
        for tool in ts.tools:
            if not isinstance(tool, ListInstancesTool):
                assert INSTANCE_PARAM_NAME in tool.parameters

    @pytest.mark.parametrize(
        "instance,host,org",
        [("a", "http://prom-a.svc:9090", "team-a"),
         ("b", "http://prom-b.svc:9090", "team-b")],
    )
    def test_each_instance_calls_its_own_prometheus(self, instance, host, org):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
            tool = next(t for t in ts.tools if t.name == "list_prometheus_rules")
            tool.invoke({INSTANCE_PARAM_NAME: instance}, create_mock_tool_invoke_context())
            last = rsps.calls[-1].request
            assert last.url.startswith(f"{host}/api/v1/rules")
            assert last.headers.get("X-Scope-OrgID") == org
