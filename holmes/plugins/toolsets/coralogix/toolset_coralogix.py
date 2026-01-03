import json
import os
from typing import Any, Optional, Tuple
from urllib.parse import quote

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.coralogix.api import (
    CoralogixTier,
    execute_dataprime_query,
    health_check,
)
from holmes.plugins.toolsets.coralogix.utils import CoralogixConfig, normalize_datetime
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


def _build_coralogix_query_url(
    config: CoralogixConfig,
    query: str,
    start_date: str,
    end_date: str,
    tier: Optional[CoralogixTier] = None,
) -> Optional[str]:
    try:
        if tier == CoralogixTier.ARCHIVE:
            data_pipeline = "archive-logs"
        else:
            # due to a bug in Coralogix, we always use the logs pipeline
            # since the tracing url does not support the query parameter
            # https://coralogix.com/docs/user-guides/monitoring-and-insights/logs-screen/query-urls/
            data_pipeline = "logs"

        time_range = f"from:{start_date},to:{end_date}"

        encoded_query = quote(query)
        encoded_time = quote(time_range)
        base_url = f"https://{config.team_hostname}.{config.domain}"

        url = (
            f"{base_url}/#/query-new/{data_pipeline}"
            f"?querySyntax=dataprime"
            f"&time={encoded_time}"
            f"&query={encoded_query}"
            f"&permalink=true"
        )
        return url

    except Exception:
        return None


class ExecuteDataPrimeQuery(Tool):
    def __init__(self, toolset: "CoralogixToolset"):
        super().__init__(
            name="coralogix_execute_dataprime_query",
            description="Execute a DataPrime query against Coralogix to fetch logs, traces, metrics, and other telemetry data. "
            "Returns the raw query results from Coralogix.",
            parameters={
                "query": ToolParameter(
                    description="DataPrime query string. Examples: `source logs | lucene 'error' | limit 100`, `source spans | lucene 'my-service' | limit 100`. Always include a `limit` clause.",
                    type="string",
                    required=True,
                ),
                "description": ToolParameter(
                    description="Brief 6-word description of the query.",
                    type="string",
                    required=True,
                ),
                "query_type": ToolParameter(
                    description="'Logs', 'Traces', 'Metrics', 'Discover Data' or 'Other'.",
                    type="string",
                    required=True,
                ),
                "start_date": ToolParameter(
                    description="Optional start date in RFC3339 format (e.g., '2024-01-01T00:00:00Z').",
                    type="string",
                    required=True,
                ),
                "end_date": ToolParameter(
                    description="Optional end date in RFC3339 format (e.g., '2024-01-01T23:59:59Z').",
                    type="string",
                    required=True,
                ),
                "tier": ToolParameter(
                    description="Optional tier: 'FREQUENT_SEARCH' or 'ARCHIVE'.",
                    type="string",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self._toolset.coralogix_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Coralogix toolset is not configured",
                params=params,
            )

        tier = None
        if tier_str := params.get("tier"):
            try:
                tier = CoralogixTier[tier_str]
            except KeyError:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Invalid tier '{tier_str}'. Must be 'FREQUENT_SEARCH' or 'ARCHIVE'",
                    params=params,
                )

        start_time = normalize_datetime(params.get("start_date"))
        end_time = normalize_datetime(params.get("end_date"))
        if start_time == "UNKNOWN_TIMESTAMP" or end_time == "UNKNOWN_TIMESTAMP":
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Invalid start or end date: {params.get('start_date')} or {params.get('end_date')}. Please provide valid dates in RFC3339 format (e.g., '2024-01-01T00:00:00Z').",
                params=params,
            )

        if start_time > end_time:
            start_time, end_time = end_time, start_time

        result, error = execute_dataprime_query(
            domain=self._toolset.coralogix_config.domain,
            api_key=self._toolset.coralogix_config.api_key,
            dataprime_query=params["query"],
            start_date=start_time,
            end_date=end_time,
            tier=tier,
        )

        if error:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error,
                params=params,
            )

        result_dict = {
            "tool_name": self.name,
            "data": result,
        }
        status = StructuredToolResultStatus.SUCCESS

        if not result:
            results_msg = "No results found, it is possible that the query is not correct, using incorrect labels or filters."
            result_dict["results_msg"] = results_msg
            status = StructuredToolResultStatus.NO_DATA

        # Build Coralogix query URL
        explore_url = _build_coralogix_query_url(
            config=self._toolset.coralogix_config,
            query=params["query"],
            start_date=start_time,
            end_date=end_time,
            tier=tier,
        )

        # Return a pretty-printed JSON string for readability by the model/user.
        final_result = json.dumps(result_dict, indent=2, sort_keys=False)
        return StructuredToolResult(
            status=status,
            data=final_result,
            params=params,
            url=explore_url,
        )

    def get_parameterized_one_liner(self, params) -> str:
        description = params.get("description", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Execute DataPrime ({description})"


class CoralogixToolset(Toolset):
    def __init__(self):
        super().__init__(
            name="coralogix",
            description="Toolset for interacting with Coralogix to fetch logs, traces, metrics, and execute DataPrime queries",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/coralogix-logs/",
            icon_url="https://avatars.githubusercontent.com/u/35295744?s=200&v=4",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[ExecuteDataPrimeQuery(self)],
            tags=[ToolsetTag.CORE],
        )
        template_path = os.path.join(os.path.dirname(__file__), "coralogix.jinja2")
        if os.path.exists(template_path):
            self._load_llm_instructions(
                jinja_template=f"file://{os.path.abspath(template_path)}"
            )

    def get_example_config(self):
        example_config = CoralogixConfig(
            api_key="<cxuw_...>", team_hostname="my-team", domain="eu2.coralogix.com"
        )
        return example_config.model_dump()

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, TOOLSET_CONFIG_MISSING_ERROR

        self.config = CoralogixConfig(**config)

        if not self.config.api_key:
            return False, "Missing configuration field 'api_key'"

        return health_check(domain=self.config.domain, api_key=self.config.api_key)

    @property
    def coralogix_config(self) -> Optional[CoralogixConfig]:
        return self.config
