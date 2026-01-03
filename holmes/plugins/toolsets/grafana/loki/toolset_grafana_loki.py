import json
import os
from typing import Dict, Optional, Tuple
from urllib.parse import quote

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.consts import (
    STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
)
from holmes.plugins.toolsets.grafana.common import GrafanaConfig, get_base_url
from holmes.plugins.toolsets.grafana.loki_api import (
    execute_loki_query,
)
from holmes.plugins.toolsets.grafana.toolset_grafana import BaseGrafanaToolset
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_LOG_LIMIT,
    DEFAULT_TIME_SPAN_SECONDS,
)
from holmes.plugins.toolsets.utils import (
    process_timestamps_to_rfc3339,
    standard_start_datetime_tool_param_description,
    toolset_name_for_one_liner,
)


def _build_grafana_loki_explore_url(
    config: GrafanaConfig, query: str, start: str, end: str, limit: int = 100
) -> Optional[str]:
    if not config.grafana_datasource_uid:
        return None
    try:
        base_url = config.external_url or config.url
        datasource_uid = config.grafana_datasource_uid or "loki"

        from_str = start if start else "now-1h"
        to_str = end if end else "now"

        pane_id = "tmp"
        safe_query = query if query else "{}"
        panes = {
            pane_id: {
                "datasource": datasource_uid,
                "queries": [
                    {
                        "refId": "A",
                        "datasource": {"type": "loki", "uid": datasource_uid},
                        "expr": safe_query,
                        "queryType": "range",
                        "maxLines": limit,
                    }
                ],
                "range": {"from": from_str, "to": to_str},
            }
        }

        panes_encoded = quote(
            json.dumps(panes, separators=(",", ":"), ensure_ascii=False), safe=""
        )
        return f"{base_url}/explore?schemaVersion=1&panes={panes_encoded}&orgId=1"
    except Exception:
        return None


class GrafanaLokiToolset(BaseGrafanaToolset):
    def health_check(self) -> Tuple[bool, str]:
        """Test a dummy query to check if service available."""
        (start, end) = process_timestamps_to_rfc3339(
            start_timestamp=-1,
            end_timestamp=None,
            default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
        )

        c = self._grafana_config
        try:
            _ = execute_loki_query(
                base_url=get_base_url(c),
                api_key=c.api_key,
                headers=c.headers,
                query='{job="test_endpoint"}',
                start=start,
                end=end,
                limit=1,
            )
        except Exception as e:
            return False, f"Unable to connect to Loki.\n{str(e)}"
        return True, ""

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
        query_str = params.get("query", '{query="no_query_fallback"}')
        try:
            data = execute_loki_query(
                base_url=get_base_url(config),
                api_key=config.api_key,
                headers=config.headers,
                query=query_str,
                start=start,
                end=end,
                limit=params.get("limit") or DEFAULT_LOG_LIMIT,
            )

            explore_url = _build_grafana_loki_explore_url(
                config,
                query_str,
                start,
                end,
                limit=params.get("limit") or DEFAULT_LOG_LIMIT,
            )

            if data:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=data,
                    params=params,
                    url=explore_url,
                )
            else:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params=params,
                    url=explore_url,
                )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                params=params,
                error=str(e),
                url=f"{get_base_url(config)}/loki/api/v1/query_range",
            )
