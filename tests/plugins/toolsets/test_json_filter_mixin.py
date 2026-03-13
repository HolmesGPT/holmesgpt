from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.plugins.toolsets.grafana.toolset_grafana import (
    GetDashboardByUID,
    GrafanaDashboardConfig,
    GrafanaToolset,
)
from holmes.plugins.toolsets.json_filter_mixin import _truncate_to_depth


def _build_tool(data):
    toolset = GrafanaToolset()
    toolset._grafana_config = GrafanaDashboardConfig(url="http://example.com")
    tool = GetDashboardByUID(toolset)
    tool._make_grafana_request = lambda endpoint, params: StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS,
        data=data,
        params=params,
        url="http://api",
    )
    return tool


def test_truncate_to_depth_limits_nested_values():
    data = {"a": {"b": {"c": 1}}, "list": [1, {"nested": 2}]}
    truncated = _truncate_to_depth(data, 1)
    assert truncated["a"] == "...truncated at depth 1"
    assert truncated["list"] == "...truncated at depth 1"


def test_jq_filter_applies_before_returning_data():
    data = {"dashboard": {"panels": [{"id": 1, "title": "CPU"}]}}
    tool = _build_tool(data)

    result = tool._invoke(
        {"uid": "abc", "jq": ".dashboard.panels[].title"}, context=None
    )

    assert result.status is StructuredToolResultStatus.SUCCESS
    assert result.data == "CPU"


def test_invalid_jq_returns_data_with_error_hint():
    data = {"dashboard": {"panels": [{"id": 1, "title": "CPU"}]}}
    tool = _build_tool(data)

    result = tool._invoke({"uid": "abc", "jq": ".["}, context=None)

    # Invalid jq should return SUCCESS with the raw data preview and error hint
    # so the LLM can see the response shape and self-correct its expression
    assert result.status is StructuredToolResultStatus.SUCCESS
    assert "jq_error" in result.data
    assert "Invalid jq expression" in result.data["jq_error"]
    assert "raw_response_preview" in result.data
    # Preview is a size-capped string, not a dict
    assert isinstance(result.data["raw_response_preview"], str)
    assert "dashboard" in result.data["raw_response_preview"]


def test_depth_applies_after_filters():
    data = {"dashboard": {"panels": [{"id": 1, "title": "CPU"}]}}
    tool = _build_tool(data)

    result = tool._invoke({"uid": "abc", "max_depth": 1}, context=None)

    assert result.data["dashboard"] == "...truncated at depth 1"
