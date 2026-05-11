import json
import os
from typing import ClassVar, Dict, List, Optional, Tuple, Type
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
from holmes.plugins.toolsets.grafana.base_grafana_toolset import (
    GRAFANA_INSTANCE_PARAM_DESCRIPTION,
    BaseGrafanaToolset,
)
from holmes.plugins.toolsets.grafana.common import (
    DirectLokiConfig,
    GrafanaCloudLokiConfig,
    GrafanaConfig,
    GrafanaInstance,
    GrafanaLokiProxyConfig,
    build_auth,
    get_base_url,
)
from holmes.plugins.toolsets.grafana.loki_api import (
    execute_loki_query,
)
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_LOG_LIMIT,
    DEFAULT_TIME_SPAN_SECONDS,
)
from holmes.plugins.toolsets.utils import (
    process_timestamps_to_rfc3339,
    standard_start_datetime_tool_param_description,
    toolset_name_for_one_liner,
)

GRAFANA_INSTANCE_PARAM = ToolParameter(
    type="string",
    description=GRAFANA_INSTANCE_PARAM_DESCRIPTION,
    required=False,
)


def _build_grafana_loki_explore_url(
    instance: GrafanaInstance, query: str, start: str, end: str, limit: int = 100
) -> Optional[str]:
    if not instance.grafana_datasource_uid:
        return None
    try:
        base_url = instance.external_url or instance.api_url
        datasource_uid = instance.grafana_datasource_uid or "loki"

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
    # base_grafana_toolset tries each class in order and uses the first that
    # validates. The proxy variant is listed first because it matches the
    # recommended path in the docs and existing configs with grafana_datasource_uid
    # continue to parse successfully against it.
    config_classes: ClassVar[List[Type[GrafanaConfig]]] = [
        GrafanaLokiProxyConfig,
        DirectLokiConfig,
        GrafanaCloudLokiConfig,
    ]

    def health_check(self) -> Tuple[bool, str]:
        """Test a dummy query against every configured Loki instance."""
        (start, end) = process_timestamps_to_rfc3339(
            start_timestamp=-1,
            end_timestamp=None,
            default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
        )
        failures: List[str] = []
        for instance in self._instances.values():
            try:
                _ = execute_loki_query(
                    base_url=get_base_url(instance),
                    api_key=instance.api_key,
                    headers=instance.additional_headers,
                    auth=build_auth(instance),
                    query='{job="test_endpoint"}',
                    start=start,
                    end=end,
                    limit=1,
                    verify_ssl=bool(instance.verify_ssl),
                    timeout=instance.timeout_seconds,
                    max_retries=instance.max_retries,
                )
            except Exception as e:
                failures.append(f"[{instance.name}] Unable to connect to Loki: {e}")
        return self._aggregate_health_results(failures, len(self._instances))

    def __init__(self):
        super().__init__(
            name="grafana/loki",
            description="Runs loki log queries using Grafana Loki or Loki directly.",
            icon_url="https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/grafana.svg",
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
        "grafana_instance": GRAFANA_INSTANCE_PARAM,
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
        try:
            instance = self.toolset._get_instance(params)
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )

        (start, end) = process_timestamps_to_rfc3339(
            start_timestamp=params.get("start"),
            end_timestamp=params.get("end"),
            default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
        )

        query_str = params.get("query", '{query="no_query_fallback"}')
        limit = params.get("limit") or DEFAULT_LOG_LIMIT
        try:
            data = execute_loki_query(
                base_url=get_base_url(instance),
                api_key=instance.api_key,
                headers=instance.additional_headers,
                auth=build_auth(instance),
                query=query_str,
                start=start,
                end=end,
                limit=limit,
                verify_ssl=bool(instance.verify_ssl),
                timeout=instance.timeout_seconds,
                max_retries=instance.max_retries,
            )

            explore_url = _build_grafana_loki_explore_url(
                instance, query_str, start, end, limit=limit,
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
                url=f"{get_base_url(instance)}/loki/api/v1/query_range",
            )
