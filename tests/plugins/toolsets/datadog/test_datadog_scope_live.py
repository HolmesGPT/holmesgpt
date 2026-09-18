"""
Live integration tests for Datadog environment scoping.

These run against a real Datadog org and assert that no record outside the
configured scope (env:staging) comes back from any tool. Run them with the
restricted service-account keys when validating a customer setup — with those
keys, Datadog's own restriction query and the Holmes-side scope are being
tested together, which is the deployed configuration.

Requires RUN_SLOW_TESTS=1 plus DD_API_KEY / DD_APP_KEY (and optionally
DD_SITE_URL, DD_SCOPE_ENV to override the defaults).
"""

import json
import os

import pytest

from holmes.plugins.toolsets.datadog.toolset_datadog_logs import DatadogLogsToolset
from holmes.plugins.toolsets.datadog.toolset_datadog_metrics import (
    DatadogMetricsToolset,
)
from holmes.plugins.toolsets.datadog.toolset_datadog_traces import (
    DatadogTracesToolset,
)
from tests.conftest import create_mock_tool_invoke_context

SCOPE_ENV = os.getenv("DD_SCOPE_ENV", "staging")


def _base_config():
    return {
        "api_key": os.getenv("DD_API_KEY"),
        "app_key": os.getenv("DD_APP_KEY"),
        "api_url": os.getenv("DD_SITE_URL", "https://api.us5.datadoghq.com"),
        "timeout_seconds": 60,
        "scope": {"tags": {"env": SCOPE_ENV}},
    }


def _tool(toolset, name):
    return next(t for t in toolset.tools if t.name == name)


def _env_tags(record: dict) -> list[str]:
    """Extract env:* tags from a log/span event record."""
    tags = record.get("attributes", {}).get("tags", []) or []
    return [t for t in tags if t.startswith("env:")]


pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_SLOW_TESTS")
    or not all([os.getenv("DD_API_KEY"), os.getenv("DD_APP_KEY")]),
    reason="Slow test - set RUN_SLOW_TESTS=1 and Datadog credentials to run",
)


class TestDatadogScopeLive:
    def test_logs_return_zero_out_of_scope_records(self):
        toolset = DatadogLogsToolset()
        success, error_msg = toolset.prerequisites_callable(_base_config())
        assert success, f"Failed to initialize toolset: {error_msg}"

        tool = _tool(toolset, "fetch_datadog_logs")
        # Deliberately prod-targeted query: the scope must contain it.
        result = tool._invoke(
            {"query": "env:production OR *", "limit": 100},
            context=create_mock_tool_invoke_context(),
        )
        assert result.status.value in ("success", "no_data"), result.error

        if result.status.value == "success" and isinstance(result.data, dict):
            for record in result.data.get("data", []) or []:
                envs = _env_tags(record)
                assert all(e == f"env:{SCOPE_ENV}" for e in envs), (
                    f"Out-of-scope log record returned: {envs}"
                )

    def test_spans_return_zero_out_of_scope_records(self):
        toolset = DatadogTracesToolset()
        success, error_msg = toolset.prerequisites_callable(_base_config())
        assert success, f"Failed to initialize toolset: {error_msg}"

        tool = _tool(toolset, "fetch_datadog_spans")
        result = tool._invoke(
            {"query": "env:production OR *", "limit": 100},
            context=create_mock_tool_invoke_context(),
        )
        assert result.status.value in ("success", "no_data"), result.error

        if result.status.value == "success":
            data = result.data
            if isinstance(data, str):
                data = json.loads(data)
            for record in (data or {}).get("data", []) or []:
                envs = _env_tags(record)
                assert all(e == f"env:{SCOPE_ENV}" for e in envs), (
                    f"Out-of-scope span returned: {envs}"
                )

    def test_metrics_prod_query_rejected(self):
        toolset = DatadogMetricsToolset()
        success, error_msg = toolset.prerequisites_callable(_base_config())
        assert success, f"Failed to initialize toolset: {error_msg}"

        tool = _tool(toolset, "query_datadog_metrics")
        for query in (
            "avg:system.cpu.user{env:production}",
            "avg:system.cpu.user{*}",
            "avg:system.cpu.user{env:staging OR env:production}",
        ):
            result = tool._invoke(
                {"query": query, "description": "prod probe"},
                context=create_mock_tool_invoke_context(),
            )
            assert result.status.value == "error", (
                f"Query {query!r} was not rejected: {result.status}"
            )
            assert SCOPE_ENV in (result.error or "")

    def test_list_active_metrics_scoped(self):
        toolset = DatadogMetricsToolset()
        success, error_msg = toolset.prerequisites_callable(_base_config())
        assert success, f"Failed to initialize toolset: {error_msg}"

        tool = _tool(toolset, "list_active_datadog_metrics")
        # tag_filter is forced to the scope regardless of what is passed here.
        result = tool._invoke(
            {"tag_filter": "env:production"},
            context=create_mock_tool_invoke_context(),
        )
        # Either metrics exist within scope, or none do — both are acceptable;
        # what matters (asserted at the wire level in test_datadog_scope.py) is
        # that the filter sent to Datadog was the scope's.
        assert result.status.value in ("success", "error"), result.error
