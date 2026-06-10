"""Multi-instance proof for VictoriaLogs through the actual wrapper.

VictoriaLogs is unchanged from master (single-instance); it becomes multi-instance
purely by `multi_instance(VictoriaLogsToolset)` at registration. Auth is bearer
token OR basic. HTTP is mocked; routed calls are asserted on the wire.
"""

import pytest
import responses

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from holmes.plugins.toolsets.victorialogs.victorialogs import VictoriaLogsToolset
from tests.conftest import create_mock_tool_invoke_context

PROD = "https://vl-prod.example.com"
DEV = "http://vl-dev.svc:9428"


def _health(rsps, api_url):
    rsps.add(responses.GET, f"{api_url}/health", body="OK", status=200)


class TestVictoriaLogsFlat:
    def test_flat_config_backwards_compatible(self):
        ts = multi_instance(VictoriaLogsToolset)
        with responses.RequestsMock() as rsps:
            _health(rsps, PROD)
            ok, _ = ts.prerequisites_callable({"api_url": PROD})
        assert ok is True
        assert list(ts._children) == ["default"]
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)
        assert all(INSTANCE_PARAM_NAME not in t.parameters for t in ts.tools)


class TestVictoriaLogsMultiInstance:
    def _build(self, rsps):
        _health(rsps, PROD)
        _health(rsps, DEV)
        ts = multi_instance(VictoriaLogsToolset)
        ok, _ = ts.prerequisites_callable(
            {
                "instances": [
                    {"name": "prod", "api_url": PROD, "bearer_token": "tok_prod"},
                    {"name": "dev", "api_url": DEV},
                ]
            }
        )
        assert ok is True
        return ts

    def test_tool_surface(self):
        with responses.RequestsMock() as rsps:
            ts = self._build(rsps)
        assert any(t.name == "victorialogs_list_instances" for t in ts.tools)
        for tool in ts.tools:
            if not isinstance(tool, ListInstancesTool):
                assert INSTANCE_PARAM_NAME in tool.parameters

    @pytest.mark.parametrize(
        "instance,host,expect_auth",
        [
            ("prod", PROD, "Bearer tok_prod"),
            ("dev", DEV, None),
        ],
    )
    def test_each_instance_calls_its_own_endpoint(self, instance, host, expect_auth):
        with responses.RequestsMock() as rsps:
            ts = self._build(rsps)
            rsps.add(
                responses.POST, f"{host}/select/logsql/query", body="", status=200
            )
            query = next(t for t in ts.tools if t.name == "victorialogs_query")
            result = query.invoke(
                {"query": "*", INSTANCE_PARAM_NAME: instance},
                create_mock_tool_invoke_context(),
            )
            assert result.status in (
                StructuredToolResultStatus.SUCCESS,
                StructuredToolResultStatus.NO_DATA,
            )
            last = rsps.calls[-1].request
            assert last.url.startswith(f"{host}/select/logsql/query")
            assert last.headers.get("Authorization") == expect_auth
