from typing import Dict
import os
import json
from holmes.core.tools import (
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.consts import (
    STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
)

from holmes.plugins.toolsets.grafana.common import get_base_url
from holmes.plugins.toolsets.grafana.toolset_grafana import BaseGrafanaToolset
from holmes.plugins.toolsets.utils import (
    process_timestamps_to_rfc3339,
    standard_start_datetime_tool_param_description,
)
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_TIME_SPAN_SECONDS,
    DEFAULT_LOG_LIMIT,
)
from holmes.plugins.toolsets.grafana.loki_api import (
    execute_loki_query,
)

from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus


class GrafanaLokiToolset(BaseGrafanaToolset):
    def __init__(self):
        super().__init__(
            name="grafana/loki",
            description="Runs loki log queries using Grafana Loki or Loki directly.",
            icon_url="https://grafana.com/media/docs/loki/logo-grafana-loki.png",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/grafanaloki/",
            tools=[],
        )

        self.tools = [LokiQuery(toolset=self)]
        instructions_filepath = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{instructions_filepath}")


class LokiQuery(Tool):
    toolset: GrafanaLokiToolset
    name: str = "grafana_loki_query"
    description: str = "Run a query against Grafana Loki using LogQL query language."
    parameters: Dict[str, ToolParameter] = {
        "query": ToolParameter(
            description="LogQL query string.",
            type="string",
            required=True,
        ),
        "start": ToolParameter(
            description=standard_start_datetime_tool_param_description(
                DEFAULT_TIME_SPAN_SECONDS
            ),
            type="string",
            required=False,
        ),
        "end": ToolParameter(
            description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
            type="string",
            required=False,
        ),
        "limit": ToolParameter(
            description=f"Maximum number of entries to return (default: {DEFAULT_LOG_LIMIT})",
            type="integer",
            required=False,
        ),
    }

    def get_parameterized_one_liner(self, params) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: loki query {params}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        (start, end) = process_timestamps_to_rfc3339(
            start_timestamp=params.get("start"),
            end_timestamp=params.get("end"),
            default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
        )

        config = self.toolset._grafana_config
        try:
            data = execute_loki_query(
                base_url=get_base_url(config),
                api_key=config.api_key,
                headers=config.headers,
                query=params.get(
                    "query", '{query="no_query_fallback"}'
                ),  # make sure a string returns. fall back to query that will return nothing.
                start=start,
                end=end,
                limit=params.get("limit") or DEFAULT_LOG_LIMIT,
            )
            if data:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=json.dumps(data),
                    params=params,
                )
            else:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params=params,
                )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                params=params,
                error=str(e),
                url=f"{get_base_url(config)}/loki/api/v1/query_range",
            )
