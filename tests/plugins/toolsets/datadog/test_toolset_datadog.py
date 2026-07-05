"""Tests for the unified (umbrella) Datadog toolset.

The umbrella composes the four specialized Datadog sub-toolsets (logs, metrics,
traces, general) under a single shared credential config. These tests verify
that composition: tool aggregation, per-area health handling (tolerant), the
de-collided limit mapping, and approval-gating forwarding.
"""

from unittest.mock import patch

from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.datadog.datadog_models import (
    DEFAULT_METRICS_LIMIT,
)
from holmes.plugins.toolsets.datadog.toolset_datadog import (
    DatadogConfig,
    DatadogToolset,
)
from holmes.plugins.toolsets.logging_utils.logging_api import DEFAULT_LOG_LIMIT

VALID_CONFIG = {
    "api_key": "test-api-key",
    "app_key": "test-app-key",
    "api_url": "https://api.datadoghq.com",
}

# Every tool the four sub-toolsets contribute, so the umbrella surface is exhaustive.
EXPECTED_TOOL_NAMES = {
    "fetch_datadog_logs",
    "list_active_datadog_metrics",
    "query_datadog_metrics",
    "get_datadog_metric_metadata",
    "list_datadog_metric_tags",
    "fetch_datadog_spans",
    "aggregate_datadog_spans",
    "datadog_api_get",
    "datadog_api_post_search",
    "list_datadog_api_resources",
}


def _patch_all_healthy(stack):
    """Enter patches that make all four areas' health checks succeed."""
    # logs + traces just need a non-raising response; metrics + general read
    # `.get("valid")`, so return a dict that validates.
    stack.enter_context(
        patch(
            "holmes.plugins.toolsets.datadog.toolset_datadog_logs.execute_datadog_http_request",
            return_value={},
        )
    )
    stack.enter_context(
        patch(
            "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request",
            return_value={},
        )
    )
    stack.enter_context(
        patch(
            "holmes.plugins.toolsets.datadog.toolset_datadog_metrics.execute_datadog_http_request",
            return_value={"valid": True},
        )
    )
    stack.enter_context(
        patch(
            "holmes.plugins.toolsets.datadog.toolset_datadog_general.execute_datadog_http_request",
            return_value={"valid": True},
        )
    )
    # The general toolset fetches the Datadog OpenAPI spec on startup; avoid network.
    stack.enter_context(
        patch(
            "holmes.plugins.toolsets.datadog.toolset_datadog_general.fetch_openapi_spec",
            return_value={"paths": {}},
        )
    )


def test_missing_config():
    toolset = DatadogToolset()
    toolset.config = None
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert "Missing config" in (toolset.error or "")


def test_invalid_config_missing_required_fields():
    toolset = DatadogToolset()
    toolset.config = {"api_key": "only-key"}  # missing app_key + api_url
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert "Invalid Datadog configuration" in (toolset.error or "")


def test_all_areas_healthy_exposes_all_tools():
    from contextlib import ExitStack

    toolset = DatadogToolset()
    toolset.config = dict(VALID_CONFIG)
    with ExitStack() as stack:
        _patch_all_healthy(stack)
        toolset.check_prerequisites()

    assert toolset.status == ToolsetStatusEnum.ENABLED
    # Fully healthy → no error surfaced.
    assert toolset.error is None
    tool_names = {t.name for t in toolset.tools}
    assert tool_names == EXPECTED_TOOL_NAMES


def test_partial_health_still_enabled_and_surfaces_failure():
    """Logs/traces/general healthy but metrics invalid → still enabled, and the
    failing area is surfaced in the (non-fatal) message."""
    from contextlib import ExitStack

    toolset = DatadogToolset()
    toolset.config = dict(VALID_CONFIG)
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "holmes.plugins.toolsets.datadog.toolset_datadog_logs.execute_datadog_http_request",
                return_value={},
            )
        )
        stack.enter_context(
            patch(
                "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request",
                return_value={},
            )
        )
        # metrics reports invalid key → its health check fails
        stack.enter_context(
            patch(
                "holmes.plugins.toolsets.datadog.toolset_datadog_metrics.execute_datadog_http_request",
                return_value={"valid": False},
            )
        )
        stack.enter_context(
            patch(
                "holmes.plugins.toolsets.datadog.toolset_datadog_general.execute_datadog_http_request",
                return_value={"valid": True},
            )
        )
        stack.enter_context(
            patch(
                "holmes.plugins.toolsets.datadog.toolset_datadog_general.fetch_openapi_spec",
                return_value={"paths": {}},
            )
        )
        toolset.check_prerequisites()

    assert toolset.status == ToolsetStatusEnum.ENABLED
    assert "[metrics]" in (toolset.error or "")
    # Tools from every area (including the failed one) are still registered.
    assert {t.name for t in toolset.tools} == EXPECTED_TOOL_NAMES


def test_all_areas_fail_disables_toolset():
    from contextlib import ExitStack

    toolset = DatadogToolset()
    toolset.config = dict(VALID_CONFIG)
    with ExitStack() as stack:
        for module in ("logs", "traces", "metrics", "general"):
            stack.enter_context(
                patch(
                    f"holmes.plugins.toolsets.datadog.toolset_datadog_{module}.execute_datadog_http_request",
                    side_effect=Exception("boom"),
                )
            )
        stack.enter_context(
            patch(
                "holmes.plugins.toolsets.datadog.toolset_datadog_general.fetch_openapi_spec",
                return_value={"paths": {}},
            )
        )
        toolset.check_prerequisites()

    assert toolset.status == ToolsetStatusEnum.FAILED
    assert toolset.error


def test_decollided_limits_map_to_correct_subconfig():
    """logs_default_limit / metrics_default_limit must land on the right area."""
    from contextlib import ExitStack

    toolset = DatadogToolset()
    toolset.config = {
        **VALID_CONFIG,
        "logs_default_limit": 4242,
        "metrics_default_limit": 77,
    }
    with ExitStack() as stack:
        _patch_all_healthy(stack)
        toolset.check_prerequisites()

    assert toolset.status == ToolsetStatusEnum.ENABLED
    by_name = {sub.name: sub for sub in toolset._subtoolsets}
    assert by_name["datadog/logs"].dd_config.default_limit == 4242
    assert by_name["datadog/metrics"].dd_config.default_limit == 77


def test_limit_defaults_are_independent():
    """Without overrides, each area keeps its own default (they collide by name
    in the sub-configs but not in the unified config)."""
    from contextlib import ExitStack

    toolset = DatadogToolset()
    toolset.config = dict(VALID_CONFIG)
    with ExitStack() as stack:
        _patch_all_healthy(stack)
        toolset.check_prerequisites()

    by_name = {sub.name: sub for sub in toolset._subtoolsets}
    assert by_name["datadog/logs"].dd_config.default_limit == DEFAULT_LOG_LIMIT
    assert by_name["datadog/metrics"].dd_config.default_limit == DEFAULT_METRICS_LIMIT


def test_approval_required_tools_forwarded_to_subtoolsets():
    from contextlib import ExitStack

    toolset = DatadogToolset()
    toolset.config = dict(VALID_CONFIG)
    toolset.approval_required_tools = ["datadog_api_post_search"]
    with ExitStack() as stack:
        _patch_all_healthy(stack)
        toolset.check_prerequisites()

    for sub in toolset._subtoolsets:
        assert sub.approval_required_tools == ["datadog_api_post_search"]


def test_config_schema_is_single_credential_form():
    """The frontend renders one direct form: required creds + password fields,
    with list-typed advanced fields hidden."""
    toolset = DatadogToolset()
    entry = toolset.get_config_schema()["DatadogConfig"]["schema"]
    props = entry["properties"]
    assert entry["required"] == ["api_key", "app_key", "api_url"]
    assert props["api_key"]["format"] == "password"
    assert props["app_key"]["format"] == "password"
    # Hidden list fields don't render as form inputs.
    assert "logs_indexes" not in props
    assert "traces_indexes" not in props


def test_hidden_index_fields_still_map_at_runtime():
    """logs_indexes / traces_indexes are hidden from the form but still applied."""
    from contextlib import ExitStack

    toolset = DatadogToolset()
    toolset.config = {
        **VALID_CONFIG,
        "logs_indexes": ["logs-main"],
        "traces_indexes": ["trace-main"],
    }
    with ExitStack() as stack:
        _patch_all_healthy(stack)
        toolset.check_prerequisites()

    by_name = {sub.name: sub for sub in toolset._subtoolsets}
    assert by_name["datadog/logs"].dd_config.indexes == ["logs-main"]
    assert by_name["datadog/traces"].dd_config.indexes == ["trace-main"]


def test_datadog_config_accepts_legacy_credential_aliases():
    """Deprecated dd_api_key/dd_app_key/site_api_url still resolve (inherited
    from DatadogBaseConfig._deprecated_mappings)."""
    cfg = DatadogConfig(
        dd_api_key="k",
        dd_app_key="a",
        site_api_url="https://api.datadoghq.eu",
    )
    assert cfg.api_key == "k"
    assert cfg.app_key == "a"
    assert str(cfg.api_url).rstrip("/") == "https://api.datadoghq.eu"
