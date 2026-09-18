"""
Quick test to invoke GetLogs and measure token count.
Useful for finding optimal limit values empirically.

Run with:
    DD_API_KEY=xxx DD_APP_KEY=xxx pytest tests/plugins/toolsets/datadog/logs/test_fetch_logs.py -v -s
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.tools_utils.token_counting import count_tool_response_tokens
from holmes.plugins.toolsets.datadog.toolset_datadog_logs import (
    DatadogLogsToolset,
    GetLogs,
)
from tests.conftest import create_mock_tool_invoke_context


@pytest.mark.skipif(
    not all([os.getenv("DD_API_KEY"), os.getenv("DD_APP_KEY")]),
    reason="Datadog API credentials not available",
)
class TestGetLogsTokenCount:
    def setup_method(self):
        self.config = {
            "api_key": os.getenv("DD_API_KEY"),
            "app_key": os.getenv("DD_APP_KEY"),
            "api_url": os.getenv("DD_SITE_URL", "https://api.us5.datadoghq.com"),
            "default_limit": 150,
        }
        self.toolset = DatadogLogsToolset()
        success, error = self.toolset.prerequisites_callable(self.config)
        assert success, f"Setup failed: {error}"
        self.tool = next(t for t in self.toolset.tools if isinstance(t, GetLogs))

    def test_getlogs_token_count(self):
        params = {
            "query": "*",
            "limit": 150,
            "start_datetime": "-3600000",  # 1 hour ago
            # "end_datetime": None,
        }

        ctx = create_mock_tool_invoke_context()
        result = self.tool._invoke(params, context=ctx)

        if result.status.value == "success":
            tokens = count_tool_response_tokens(
                llm=ctx.llm,
                structured_tool_result=result,
                tool_call_id="test",
                tool_name="fetch_datadog_logs",
            )
            print(f"TOKEN COUNT: {tokens}")
            print(f"URL: {result.url}")
            print(f"\nData preview:\n{str(result.data)[:1000]}...")
        else:
            print(f"Error: {result.error}")


class TestGetLogsSort:
    """Unit tests for the sort order of the Datadog logs search."""

    def setup_method(self):
        self.toolset = DatadogLogsToolset()
        self.toolset.dd_config = MagicMock()
        self.toolset.dd_config.api_url = "https://api.datadoghq.com"
        self.toolset.dd_config.default_limit = 100
        self.toolset.dd_config.storage_tier = "indexes"
        self.toolset.dd_config.indexes = []
        self.toolset.dd_config.timeout_seconds = 60
        self.toolset.dd_config.compact_logs = False
        self.tool = GetLogs(toolset=self.toolset)

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_logs.execute_datadog_http_request"
    )
    def test_sort_desc(self, mock_execute):
        """sort_desc must map to the correct Datadog sort direction.

        Regression test: the mapping used to be inverted (sort_desc=True
        produced ascending order) and the documented default (true/descending)
        did not match the code default.
        """
        mock_execute.return_value = {"data": []}

        def sort_for(params):
            self.tool._invoke(params, context=create_mock_tool_invoke_context())
            return mock_execute.call_args[1]["payload_or_params"]["sort"]

        # Omitted -> default descending (newest first)
        assert sort_for({"query": "*"}) == "-timestamp"
        # Explicit True -> descending
        assert sort_for({"query": "*", "sort_desc": True}) == "-timestamp"
        # Explicit False -> ascending (oldest first)
        assert sort_for({"query": "*", "sort_desc": False}) == "timestamp"
