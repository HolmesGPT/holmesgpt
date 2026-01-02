"""
Quick test to invoke GetLogs and measure token count.
Useful for finding optimal limit values empirically.

Run with:
    DD_API_KEY=xxx DD_APP_KEY=xxx pytest tests/plugins/toolsets/datadog/logs/test_getlogs_token_count.py -v -s
"""

import os

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
            "dd_api_key": os.getenv("DD_API_KEY"),
            "dd_app_key": os.getenv("DD_APP_KEY"),
            "site_api_url": os.getenv("DD_SITE_URL", "https://api.datadoghq.eu"),
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
