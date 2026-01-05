# type: ignore
import datetime
import json
import os

import pytest

from holmes.core.tools import ToolsetStatusEnum
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.toolsets.prometheus.prometheus import PrometheusToolset
from tests.conftest import create_mock_tool_invoke_context

# Use pytest.mark.skip (not skipif) to show a single grouped skip line for the entire module
# Will show: "SKIPPED [6] module.py: reason" instead of 6 separate skip lines
if os.environ.get("PROMETHEUS_URL", None) is None:
    pytestmark = pytest.mark.skip(reason="PROMETHEUS_URL must be set")

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", None)


@pytest.fixture
def tool_executor():
    toolset = PrometheusToolset()
    toolset.enabled = True
    toolset.config = {"prometheus_url": PROMETHEUS_URL}
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.ENABLED
    tool_executor = ToolExecutor(toolsets=[toolset])
    return tool_executor


def test_list_available_metrics_exact_match(tool_executor: ToolExecutor):
    tool = tool_executor.get_tool_by_name("list_available_metrics")
    assert tool
    context = create_mock_tool_invoke_context()
    actual_output = tool.invoke({"name_filter": "kubelet_running_pods"}, context)
    print(actual_output)
    assert "kubelet_running_pods" in actual_output
    assert (
        "Number of pods that have a running pod sandbox" in actual_output
    )  # description
    assert "gauge" in actual_output  # type
    assert "node" in actual_output  # label
    assert "namespace" in actual_output  # label


def test_list_available_metrics_partial_match(tool_executor: ToolExecutor):
    tool = tool_executor.get_tool_by_name("list_available_metrics")
    assert tool
    context = create_mock_tool_invoke_context()
    actual_output = tool.invoke({"name_filter": "http"}, context)
    print(actual_output)
    assert (
        "http_requests_total | Total number of requests by method, status and handler. | counter"
        in actual_output
    )
    assert (
        "kubelet_http_requests_total | [ALPHA] Number of the http requests received since the server started | counter"
        in actual_output
    )

    # Ensure there is some common labels present in the result
    assert "endpoint" in actual_output
    assert "container" in actual_output
    assert "namespace" in actual_output
    assert "job" in actual_output
    assert "node" in actual_output
    assert "pod" in actual_output
    assert "service" in actual_output


def test_execute_prometheus_instant_query(tool_executor: ToolExecutor):
    tool = tool_executor.get_tool_by_name("execute_prometheus_instant_query")
    assert tool
    context = create_mock_tool_invoke_context()
    actual_output = tool.invoke({"query": "up"}, context)
    print(actual_output)
    assert actual_output
    parsed_output = json.loads(actual_output)
    assert parsed_output.get("status") == "success"


def test_execute_prometheus_instant_query_no_result(tool_executor: ToolExecutor):
    tool = tool_executor.get_tool_by_name("execute_prometheus_instant_query")
    assert tool
    context = create_mock_tool_invoke_context()
    actual_output = tool.invoke({"query": "this_metric_does_not_exist"}, context)
    print(actual_output)
    assert actual_output
    parsed_output = json.loads(actual_output)
    assert parsed_output.get("status") == "Failed"
    assert (
        parsed_output.get("error_message")
        == "The prometheus query returned no result. Is the query correct?"
    )


def test_execute_prometheus_range_query_no_result(tool_executor: ToolExecutor):
    tool = tool_executor.get_tool_by_name("execute_prometheus_range_query")
    assert tool
    twenty_minutes = 20 * 60
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    context = create_mock_tool_invoke_context()
    actual_output = tool.invoke(
        {
            "query": "this_metric_does_not_exist",
            "start": now - twenty_minutes,
            "end": now,
            "step": 1,
        },
        context,
    )
    print(actual_output)
    assert actual_output
    parsed_output = json.loads(actual_output)
    assert parsed_output.get("status") == "Failed"
    assert (
        parsed_output.get("error_message")
        == "The prometheus query returned no result. Is the query correct?"
    )


def test_execute_prometheus_range_query(tool_executor: ToolExecutor):
    tool = tool_executor.get_tool_by_name("execute_prometheus_instant_query")
    assert tool
    twenty_minutes = 20 * 60
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    context = create_mock_tool_invoke_context()
    actual_output = tool.invoke(
        {"query": "up", "start": now - twenty_minutes, "end": now, "step": 1}, context
    )
    print(actual_output)
    assert actual_output
    parsed_output = json.loads(actual_output)
    assert parsed_output.get("status") == "success"
    assert not parsed_output.get("error_message")
