"""Multi-instance proof for Datadog through the actual wrapper.

These tests exercise `multi_instance(DatadogLogsToolset)` — i.e. exactly what
`load_builtin_toolsets()` registers — NOT a directly-constructed toolset. Datadog
uses dual-key auth (`api_key` + `app_key`), so this also covers the generic
global-`app_key` fall-through. HTTP is mocked (no patching of internals): the real
health check runs, and a routed tool call is asserted on the wire.
"""

import re

import responses

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.datadog.toolset_datadog_logs import DatadogLogsToolset
from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from tests.conftest import create_mock_tool_invoke_context

US = "https://api.datadoghq.com"
EU = "https://api.datadoghq.eu"
SEARCH = "/api/v2/logs/events/search"
_OK_BODY = {"data": [], "meta": {"page": {"after": None}}}


def _register_search(rsps, host):
    # api_url is a pydantic AnyUrl (trailing slash), so the real URL has a double
    # slash before /api. Match host + path tolerant of slash count, and matches
    # both the health probe and later routed calls.
    rsps.add(
        responses.POST,
        re.compile(re.escape(host) + r"/+api/v2/logs/events/search"),
        json=_OK_BODY,
        status=200,
    )


class TestDatadogFlat:
    def test_flat_config_backwards_compatible(self):
        ts = multi_instance(DatadogLogsToolset)
        with responses.RequestsMock() as rsps:
            _register_search(rsps, US)
            ok, _ = ts.prerequisites_callable(
                {"api_key": "k", "app_key": "a", "api_url": US}
            )
        assert ok is True
        assert list(ts._children) == ["default"]
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)
        assert all(INSTANCE_PARAM_NAME not in t.parameters for t in ts.tools)


class TestDatadogMultiInstance:
    def _build(self, rsps):
        _register_search(rsps, US)
        _register_search(rsps, EU)
        ts = multi_instance(DatadogLogsToolset)
        ok, _ = ts.prerequisites_callable(
            {
                "app_key": "GLOBAL_APP",  # shared across instances
                "instances": [
                    {"name": "us", "api_url": US, "api_key": "k_us"},
                    {"name": "eu", "api_url": EU, "api_key": "k_eu", "app_key": "eu_app"},
                ],
            }
        )
        assert ok is True
        return ts

    def test_decomposition_and_global_fallthrough(self):
        with responses.RequestsMock() as rsps:
            ts = self._build(rsps)
        us = ts._children["us"].dd_config
        eu = ts._children["eu"].dd_config
        assert us.api_key == "k_us"
        assert us.app_key == "GLOBAL_APP"  # inherited global
        assert eu.api_key == "k_eu"
        assert eu.app_key == "eu_app"  # per-instance override

    def test_tool_surface(self):
        with responses.RequestsMock() as rsps:
            ts = self._build(rsps)
        assert any(t.name == "datadog_logs_list_instances" for t in ts.tools)
        for tool in ts.tools:
            if not isinstance(tool, ListInstancesTool):
                assert INSTANCE_PARAM_NAME in tool.parameters

    def test_routed_call_hits_selected_instance_on_the_wire(self):
        """The real meaningful check: a call routed to `eu` must POST to the EU
        host with EU's credentials — proving the wrapper delegates to the right
        child end-to-end (not a directly-constructed toolset)."""
        with responses.RequestsMock() as rsps:
            ts = self._build(rsps)
            fetch = next(t for t in ts.tools if t.name == "fetch_datadog_logs")
            result = fetch.invoke(
                {"query": "service:web", INSTANCE_PARAM_NAME: "eu"},
                create_mock_tool_invoke_context(),
            )
            # routing must not have errored on instance resolution
            assert result.status in (
                StructuredToolResultStatus.SUCCESS,
                StructuredToolResultStatus.NO_DATA,
            )
            last = rsps.calls[-1].request
            assert last.url.startswith(EU)
            assert last.headers.get("DD-API-KEY") == "k_eu"
            assert last.headers.get("DD-APPLICATION-KEY") == "eu_app"

    def test_routed_call_to_us_uses_us_credentials(self):
        with responses.RequestsMock() as rsps:
            ts = self._build(rsps)
            fetch = next(t for t in ts.tools if t.name == "fetch_datadog_logs")
            fetch.invoke(
                {"query": "*", INSTANCE_PARAM_NAME: "us"},
                create_mock_tool_invoke_context(),
            )
            last = rsps.calls[-1].request
            assert last.url.startswith(US)
            assert last.headers.get("DD-API-KEY") == "k_us"
            assert last.headers.get("DD-APPLICATION-KEY") == "GLOBAL_APP"
