"""End-to-end multi-instance proof on the real ServiceNow toolset.

ServiceNow's own file is unchanged from master (single-instance); it becomes
multi-instance purely by being wrapped with `multi_instance(...)`. These tests
prove the flat config still works (backwards compatible) and an `instances:`
config routes to the right endpoint.
"""

import pytest
import responses

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from holmes.plugins.toolsets.servicenow_tables.servicenow_tables import (
    ServiceNowTablesToolset,
)
from tests.conftest import create_mock_tool_invoke_context


def _health(rsps, api_url):
    rsps.add(
        responses.GET,
        f"{api_url}/api/now/v2/table/sys_user",
        json={"result": [{"sys_id": "1"}]},
        status=200,
    )


class TestServiceNowFlat:
    def test_flat_config_is_backwards_compatible(self):
        ts = multi_instance(ServiceNowTablesToolset)
        with responses.RequestsMock() as rsps:
            _health(rsps, "https://acme.service-now.com")
            ok, _ = ts.prerequisites_callable(
                {"api_url": "https://acme.service-now.com", "api_key": "k"}
            )
        assert ok is True
        names = {t.name for t in ts.tools}
        assert names == {"servicenow_get_records", "servicenow_get_record"}
        # single instance -> no routing affordances
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)
        records = next(t for t in ts.tools if t.name == "servicenow_get_records")
        assert INSTANCE_PARAM_NAME not in records.parameters


class TestServiceNowMultiInstance:
    def _build(self, rsps):
        _health(rsps, "https://prod.service-now.com")
        _health(rsps, "https://dev.service-now.com")
        ts = multi_instance(ServiceNowTablesToolset)
        ok, _ = ts.prerequisites_callable(
            {
                "instances": [
                    {"name": "prod", "api_url": "https://prod.service-now.com", "api_key": "kp"},
                    {"name": "dev", "api_url": "https://dev.service-now.com", "api_key": "kd"},
                ]
            }
        )
        assert ok is True
        return ts

    def test_exposes_routing_param_and_list_tool(self):
        with responses.RequestsMock() as rsps:
            ts = self._build(rsps)
        assert any(t.name == "servicenow_tables_list_instances" for t in ts.tools)
        records = next(t for t in ts.tools if t.name == "servicenow_get_records")
        assert INSTANCE_PARAM_NAME in records.parameters

    @pytest.mark.parametrize(
        "instance,expected_host,expected_key",
        [
            ("prod", "https://prod.service-now.com", "kp"),
            ("dev", "https://dev.service-now.com", "kd"),
        ],
    )
    def test_each_instance_calls_its_own_endpoint_with_its_own_params(
        self, instance, expected_host, expected_key
    ):
        """Invoking with a given instance must hit THAT instance's URL with THAT
        instance's credentials, and forward the tool's params (table + query)."""
        with responses.RequestsMock() as rsps:
            ts = self._build(rsps)
            rsps.add(
                responses.GET,
                f"{expected_host}/api/now/v2/table/incident",
                json={"result": [{"number": "INC42"}]},
                status=200,
            )
            records = next(t for t in ts.tools if t.name == "servicenow_get_records")
            result = records.invoke(
                {
                    "table_name": "incident",
                    "sysparm_query": "active=true",
                    INSTANCE_PARAM_NAME: instance,
                },
                create_mock_tool_invoke_context(),
            )
            assert result.status is StructuredToolResultStatus.SUCCESS
            call = rsps.calls[-1].request
            # correct instance endpoint
            assert call.url.startswith(f"{expected_host}/api/now/v2/table/incident")
            # correct per-instance credential
            assert call.headers.get("x-sn-apikey") == expected_key
            # tool params forwarded on the wire
            assert "sysparm_query=active%3Dtrue" in call.url or "active=true" in call.url

    def test_list_instances_reports_both(self):
        with responses.RequestsMock() as rsps:
            ts = self._build(rsps)
        tool = next(t for t in ts.tools if isinstance(t, ListInstancesTool))
        result = tool._invoke({}, create_mock_tool_invoke_context())
        got = {i["name"]: i.get("api_url") for i in result.data["instances"]}
        assert got == {
            "prod": "https://prod.service-now.com",
            "dev": "https://dev.service-now.com",
        }
