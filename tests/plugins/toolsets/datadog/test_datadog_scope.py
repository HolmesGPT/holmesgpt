"""
Tests for Datadog environment scoping.

The adversarial table in TestMetricQueryValidation is the acceptance criterion
for the scoping feature: every entry is an evasion an LLM could plausibly emit,
and each must be either scoped on the wire or rejected before any request goes
out. The wire-level classes then assert what actually leaves the process, which
is the test that matters.

Backwards compatibility is asserted throughout: with `scope` unset, every
payload and query parameter must be byte-identical to pre-scoping behaviour.
"""

import json
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from pydantic import ValidationError

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.datadog.datadog_models import (
    DatadogGeneralConfig,
    DatadogLogsConfig,
    DatadogMetricsConfig,
    DatadogTracesConfig,
)
from holmes.plugins.toolsets.datadog.datadog_scope import (
    DatadogScopeConfig,
    apply_scope_to_search_query,
    build_metrics_tag_filter,
    build_scope_query,
    no_data_suffix,
    validate_metric_query,
)
from holmes.plugins.toolsets.datadog.toolset_datadog_general import (
    DatadogGeneralToolset,
)
from holmes.plugins.toolsets.datadog.toolset_datadog_logs import DatadogLogsToolset
from holmes.plugins.toolsets.datadog.toolset_datadog_metrics import (
    DatadogMetricsToolset,
)
from holmes.plugins.toolsets.datadog.toolset_datadog_traces import (
    DatadogTracesToolset,
)
from tests.conftest import create_mock_tool_invoke_context

API_URL = "https://api.datadoghq.com"

BASE_CONFIG = {
    "api_key": "test-api-key",
    "app_key": "test-app-key",
    "api_url": API_URL,
    "timeout_seconds": 60,
}

SCOPE = DatadogScopeConfig(tags={"env": "staging"})


# ---------------------------------------------------------------------------
# Scope config validation
# ---------------------------------------------------------------------------


class TestScopeConfig:
    def test_single_value(self):
        scope = DatadogScopeConfig(tags={"env": "staging"})
        assert scope.values_for("env") == ["staging"]
        assert scope.describe() == "env:staging"

    def test_multiple_values_and_tags(self):
        scope = DatadogScopeConfig(tags={"env": ["staging", "dev"], "region": "eu"})
        assert build_scope_query(scope) == "(env:staging OR env:dev) AND (region:eu)"
        assert scope.describe() == "env:(staging or dev), region:eu"

    def test_empty_tags_rejected(self):
        with pytest.raises(ValidationError):
            DatadogScopeConfig(tags={})

    def test_empty_value_list_rejected(self):
        with pytest.raises(ValidationError):
            DatadogScopeConfig(tags={"env": []})

    @pytest.mark.parametrize(
        "value",
        [
            "stag*",  # wildcard would defeat the scope
            "staging prod",  # whitespace could alter the query
            'staging" OR "prod',  # quote breakout
            "staging,prod",  # comma splits selector terms
            "staging)",  # paren breakout
            "",
        ],
    )
    def test_dangerous_values_rejected(self, value):
        with pytest.raises(ValidationError):
            DatadogScopeConfig(tags={"env": value})

    @pytest.mark.parametrize("key", ["env name", "env*", "env{", ""])
    def test_dangerous_keys_rejected(self, key):
        with pytest.raises(ValidationError):
            DatadogScopeConfig(tags={key: "staging"})

    def test_scope_accepted_on_all_toolset_configs(self):
        scoped = {**BASE_CONFIG, "scope": {"tags": {"env": "staging"}}}
        for config_cls in (
            DatadogLogsConfig,
            DatadogTracesConfig,
            DatadogMetricsConfig,
            DatadogGeneralConfig,
        ):
            config = config_cls(**scoped)
            assert config.scope is not None
            assert config.scope.describe() == "env:staging"

    def test_scope_defaults_to_none(self):
        for config_cls in (
            DatadogLogsConfig,
            DatadogTracesConfig,
            DatadogMetricsConfig,
            DatadogGeneralConfig,
        ):
            assert config_cls(**BASE_CONFIG).scope is None

    def test_general_rejects_scope_with_custom_endpoints(self):
        with pytest.raises(ValidationError, match="allow_custom_endpoints"):
            DatadogGeneralConfig(
                **BASE_CONFIG,
                scope={"tags": {"env": "staging"}},
                allow_custom_endpoints=True,
            )


# ---------------------------------------------------------------------------
# Search query injection (logs / traces)
# ---------------------------------------------------------------------------


class TestSearchQueryInjection:
    def test_no_scope_is_a_noop(self):
        assert apply_scope_to_search_query(None, "service:web") == "service:web"
        assert apply_scope_to_search_query(None, None) == "*"
        assert apply_scope_to_search_query(None, "") == "*"

    def test_or_is_contained_by_parentheses(self):
        result = apply_scope_to_search_query(SCOPE, "service:web OR service:api")
        assert result == "(service:web OR service:api) AND (env:staging)"

    def test_wildcard_becomes_scope(self):
        assert apply_scope_to_search_query(SCOPE, "*") == "env:staging"
        assert apply_scope_to_search_query(SCOPE, None) == "env:staging"

    def test_negation_cannot_escape(self):
        result = apply_scope_to_search_query(SCOPE, "-env:staging")
        assert result == "(-env:staging) AND (env:staging)"

    def test_env_prod_still_anded_with_scope(self):
        result = apply_scope_to_search_query(SCOPE, "env:production")
        assert result == "(env:production) AND (env:staging)"


# ---------------------------------------------------------------------------
# Metric query validation — the adversarial acceptance table
# ---------------------------------------------------------------------------


class TestMetricQueryValidation:
    @pytest.mark.parametrize(
        "query",
        [
            "avg:system.cpu.user{env:staging}",
            "avg:system.cpu.user{env:staging,host:web-1}",
            "avg:system.cpu.user{host:web-1, env:staging}",
            "avg:system.cpu.user{env:staging} by {host}",
            "sum:a.b{env:staging} + sum:c.d{env:staging}",
            "top(avg:a{env:staging} by {host}, 5, 'mean', 'desc')",
            "avg:system.cpu.user{env:staging}.rollup(sum, 60)",
            "sum:req.count{env:staging}.as_count()",
            "avg:k8s.pod.mem{env:staging,pod_name:web-*}",
            "avg:a{ENV:STAGING}",  # Datadog normalises tag case
            "week_before(avg:a{env:staging})",
            "avg:a{env:staging} by {host,service}",
            "(sum:a{env:staging} / sum:b{env:staging}) * 100",
        ],
    )
    def test_scoped_queries_accepted(self, query):
        assert validate_metric_query(SCOPE, query) is None

    @pytest.mark.parametrize(
        "query",
        [
            "avg:system.cpu.user{*}",
            "avg:system.cpu.user{env:prod}",
            "avg:system.cpu.user{env:staging OR env:prod}",
            "avg:system.cpu.user{env:staging or env:prod}",
            "avg:system.cpu.user{* OR env:prod}",
            "avg:system.cpu.user{!env:staging}",
            "avg:system.cpu.user{-env:staging}",
            "avg:system.cpu.user{NOT env:staging}",
            "avg:system.cpu.user{env:stag*}",  # wildcard near-miss
            "avg:system.cpu.user{env:staging-eu}",  # value near-miss
            "avg:system.cpu.user{env:*}",
            "avg:system.cpu.user{env IN (staging, prod)}",
            "sum:a{env:staging} + sum:b{*}",  # scope on one side only
            "sum:a{env:staging} + sum:b{env:prod}",
            "avg:a{host:x} by {env}",  # `by {env}` is grouping, not scope
            "avg:a{env:staging,!env:staging}",
            "system.cpu.user",  # no selector at all
            "avg:a{env:staging",  # unbalanced — fail closed
            "avg:a env:staging}",
            "avg:a{{env:staging}}",  # nested — fail closed
            "",
            "avg:a{}",
            "top(avg:a{*} by {host}, 5, 'mean', 'desc')",
            "avg:a{env:staging},avg:b{*}",
        ],
    )
    def test_unscoped_or_evading_queries_rejected(self, query):
        error = validate_metric_query(SCOPE, query)
        assert error is not None
        # Rejection must be actionable so the LLM can self-correct.
        assert "env:staging" in error

    def test_no_scope_accepts_everything(self):
        assert validate_metric_query(None, "avg:anything{*}") is None
        assert validate_metric_query(None, "") is None

    def test_rejection_names_the_offending_selector(self):
        error = validate_metric_query(SCOPE, "avg:system.cpu.user{host:x}")
        assert error is not None
        assert "{host:x}" in error
        assert "env:staging" in error

    def test_multi_tag_scope_requires_all_tags(self):
        scope = DatadogScopeConfig(tags={"env": ["staging", "dev"], "region": "eu"})
        assert validate_metric_query(scope, "avg:a{env:dev,region:eu}") is None
        error = validate_metric_query(scope, "avg:a{env:dev}")
        assert error is not None
        assert "region" in error

    def test_tag_filter_rendering(self):
        assert build_metrics_tag_filter(SCOPE) == "env:staging"

    def test_no_data_suffix(self):
        assert no_data_suffix(None) == ""
        assert "env:staging" in no_data_suffix(SCOPE)


# ---------------------------------------------------------------------------
# Wire-level: what actually leaves the process
# ---------------------------------------------------------------------------


def _make_logs_toolset(scope: bool) -> DatadogLogsToolset:
    config = dict(BASE_CONFIG)
    if scope:
        config["scope"] = {"tags": {"env": "staging"}}
    toolset = DatadogLogsToolset()
    toolset.dd_config = DatadogLogsConfig(**config)
    return toolset


def _make_traces_toolset(scope: bool) -> DatadogTracesToolset:
    config = dict(BASE_CONFIG)
    if scope:
        config["scope"] = {"tags": {"env": "staging"}}
    toolset = DatadogTracesToolset()
    toolset.dd_config = DatadogTracesConfig(**config)
    return toolset


def _make_metrics_toolset(scope: bool) -> DatadogMetricsToolset:
    config = dict(BASE_CONFIG)
    if scope:
        config["scope"] = {"tags": {"env": "staging"}}
    toolset = DatadogMetricsToolset()
    toolset.dd_config = DatadogMetricsConfig(**config)
    return toolset


def _tool(toolset, name):
    for tool in toolset.tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"tool {name} not found")


class TestLogsWire:
    def test_scope_injected_into_filter_query(self):
        toolset = _make_logs_toolset(scope=True)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/v2/logs/events/search",
                json={"data": [{"attributes": {"message": "x"}}]},
            )
            tool = _tool(toolset, "fetch_datadog_logs")
            result = tool._invoke(
                {"query": "service:checkout OR env:production"},
                context=create_mock_tool_invoke_context(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            sent = json.loads(rsps.calls[0].request.body)
            assert (
                sent["filter"]["query"]
                == "(service:checkout OR env:production) AND (env:staging)"
            )

    def test_cursor_does_not_bypass_scope(self):
        toolset = _make_logs_toolset(scope=True)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/v2/logs/events/search",
                json={"data": [{"attributes": {"message": "x"}}]},
            )
            tool = _tool(toolset, "fetch_datadog_logs")
            tool._invoke(
                {"query": "*", "cursor": "eyJhZnRlciI6ImV2aWwifQ=="},
                context=create_mock_tool_invoke_context(),
            )
            sent = json.loads(rsps.calls[0].request.body)
            # The cursor rides along but the filter stays scoped.
            assert sent["filter"]["query"] == "env:staging"
            assert sent["page"]["cursor"] == "eyJhZnRlciI6ImV2aWwifQ=="

    def test_empty_result_explains_scope(self):
        toolset = _make_logs_toolset(scope=True)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/v2/logs/events/search",
                json={"data": []},
            )
            tool = _tool(toolset, "fetch_datadog_logs")
            result = tool._invoke(
                {"query": "service:prod-only-service"},
                context=create_mock_tool_invoke_context(),
            )
            assert result.status == StructuredToolResultStatus.NO_DATA
            assert "env:staging" in (result.error or "")

    def test_unscoped_payload_unchanged(self):
        """Backwards compatibility: scope unset means today's payload exactly."""
        toolset = _make_logs_toolset(scope=False)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/v2/logs/events/search",
                json={"data": []},
            )
            tool = _tool(toolset, "fetch_datadog_logs")
            result = tool._invoke(
                {"query": "service:checkout"},
                context=create_mock_tool_invoke_context(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            sent = json.loads(rsps.calls[0].request.body)
            assert sent["filter"]["query"] == "service:checkout"


class TestTracesWire:
    def test_fetch_spans_scoped(self):
        toolset = _make_traces_toolset(scope=True)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/v2/spans/events/search",
                json={"data": [{"id": "span1"}]},
            )
            tool = _tool(toolset, "fetch_datadog_spans")
            result = tool._invoke(
                {"query": "service:checkout OR env:production"},
                context=create_mock_tool_invoke_context(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            sent = json.loads(rsps.calls[0].request.body)
            assert (
                sent["data"]["attributes"]["filter"]["query"]
                == "(service:checkout OR env:production) AND (env:staging)"
            )
            # The deep link a human clicks must show the same (scoped) data.
            assert "env%3Astaging" in (result.url or "")

    def test_fetch_spans_default_query_scoped(self):
        toolset = _make_traces_toolset(scope=True)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/v2/spans/events/search",
                json={"data": [{"id": "span1"}]},
            )
            tool = _tool(toolset, "fetch_datadog_spans")
            tool._invoke({}, context=create_mock_tool_invoke_context())
            sent = json.loads(rsps.calls[0].request.body)
            assert sent["data"]["attributes"]["filter"]["query"] == "env:staging"

    def test_aggregate_spans_scoped(self):
        toolset = _make_traces_toolset(scope=True)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/v2/spans/analytics/aggregate",
                json={"data": {"buckets": []}},
            )
            tool = _tool(toolset, "aggregate_datadog_spans")
            result = tool._invoke(
                {
                    "query": "env:production",
                    "compute": [{"aggregation": "count"}],
                },
                context=create_mock_tool_invoke_context(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            sent = json.loads(rsps.calls[0].request.body)
            assert (
                sent["data"]["attributes"]["filter"]["query"]
                == "(env:production) AND (env:staging)"
            )

    def test_unscoped_payload_unchanged(self):
        toolset = _make_traces_toolset(scope=False)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/v2/spans/events/search",
                json={"data": []},
            )
            tool = _tool(toolset, "fetch_datadog_spans")
            tool._invoke(
                {"query": "service:checkout"},
                context=create_mock_tool_invoke_context(),
            )
            sent = json.loads(rsps.calls[0].request.body)
            assert sent["data"]["attributes"]["filter"]["query"] == "service:checkout"


class TestMetricsWire:
    def test_unscoped_query_rejected_before_any_request(self):
        """An unscoped metric query must never reach the network."""
        toolset = _make_metrics_toolset(scope=True)
        with responses.RequestsMock() as rsps:  # no responses registered
            tool = _tool(toolset, "query_datadog_metrics")
            result = tool._invoke(
                {"query": "avg:system.cpu.user{env:production}", "description": "d"},
                context=create_mock_tool_invoke_context(),
            )
            assert result.status == StructuredToolResultStatus.ERROR
            assert "env:staging" in (result.error or "")
            assert len(rsps.calls) == 0

    def test_scoped_query_allowed(self):
        toolset = _make_metrics_toolset(scope=True)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v1/query",
                json={
                    "series": [
                        {"metric": "system.cpu.user", "pointlist": [[1000.0, 1.0]]}
                    ]
                },
            )
            tool = _tool(toolset, "query_datadog_metrics")
            result = tool._invoke(
                {
                    "query": "avg:system.cpu.user{env:staging}",
                    "description": "cpu",
                },
                context=create_mock_tool_invoke_context(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS

    def test_list_active_metrics_tag_filter_forced(self):
        toolset = _make_metrics_toolset(scope=True)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v1/metrics",
                json={"metrics": ["system.cpu.user"]},
            )
            tool = _tool(toolset, "list_active_datadog_metrics")
            result = tool._invoke(
                # The model tries to smuggle a prod filter — the scope must win.
                {"tag_filter": "env:production"},
                context=create_mock_tool_invoke_context(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            sent = parse_qs(urlparse(rsps.calls[0].request.url).query)
            assert sent["tag_filter"] == ["env:staging"]

    def test_list_active_metrics_unscoped_filter_passthrough(self):
        toolset = _make_metrics_toolset(scope=False)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v1/metrics",
                json={"metrics": ["system.cpu.user"]},
            )
            tool = _tool(toolset, "list_active_datadog_metrics")
            tool._invoke(
                {"tag_filter": "env:production"},
                context=create_mock_tool_invoke_context(),
            )
            sent = parse_qs(urlparse(rsps.calls[0].request.url).query)
            assert sent["tag_filter"] == ["env:production"]

    def test_no_data_mentions_scope(self):
        toolset = _make_metrics_toolset(scope=True)
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, f"{API_URL}/api/v1/query", json={"series": []})
            tool = _tool(toolset, "query_datadog_metrics")
            result = tool._invoke(
                {"query": "avg:system.cpu.user{env:staging}", "description": "d"},
                context=create_mock_tool_invoke_context(),
            )
            assert result.status == StructuredToolResultStatus.NO_DATA
            assert "env:staging" in (result.error or "")


class TestGeneralToolsetUnderScope:
    def test_prerequisites_fail_when_scope_configured(self):
        toolset = DatadogGeneralToolset()
        success, error = toolset.prerequisites_callable(
            {**BASE_CONFIG, "scope": {"tags": {"env": "staging"}}}
        )
        assert not success
        assert "does not support environment scoping" in error
        assert "env:staging" in error

    def test_scope_plus_custom_endpoints_fails_validation(self):
        toolset = DatadogGeneralToolset()
        success, error = toolset.prerequisites_callable(
            {
                **BASE_CONFIG,
                "scope": {"tags": {"env": "staging"}},
                "allow_custom_endpoints": True,
            }
        )
        assert not success
        assert "allow_custom_endpoints" in error
