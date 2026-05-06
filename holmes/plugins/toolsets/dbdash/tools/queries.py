from typing import TYPE_CHECKING, Dict

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.dbdash.tools.performance import (
    _TIME_RANGE_PARAMS,
    _build_time_params,
    _default_time_range,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

if TYPE_CHECKING:
    from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset


class GetSlowQueries(Tool):
    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_get_slow_queries",
            description=(
                "Get slow queries for a SQL Server instance. Returns a summary of query "
                "duration distribution and detailed list of slow queries with SQL text, "
                "duration, CPU, reads, and execution context."
            ),
            parameters={
                "instanceId": ToolParameter(
                    description="The instance ID (from dbdash_list_instances). Optional - omit to see all instances.",
                    type="string",
                    required=False,
                ),
                "from": ToolParameter(
                    description="Start datetime in ISO 8601 format. Defaults to 24 hours ago.",
                    type="string",
                    required=False,
                ),
                "to": ToolParameter(
                    description="End datetime in ISO 8601 format. Defaults to now.",
                    type="string",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            query_params = _build_time_params(params)
        except ValueError:
            default_from, default_to = _default_time_range()
            query_params = {
                "from": params.get("from", default_from),
                "to": params.get("to", default_to),
            }
            if params.get("instanceId"):
                query_params["instanceId"] = params["instanceId"]

        try:
            data = self._toolset.client.get("/api/queries/slow", params=query_params)
            if not data.get("detail") and not data.get("summary"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=f"No slow queries found for params {query_params}.",
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch slow queries from {self._toolset.dbdash_config.api_url}/api/queries/slow: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Slow Queries"


class GetRunningQueries(Tool):
    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_get_running_queries",
            description=(
                "Get currently running queries on a SQL Server instance. Returns SPID, "
                "database, login, status, duration, wait type, blocking info, and SQL text."
            ),
            parameters={
                "instanceId": ToolParameter(
                    description="The instance ID (required)",
                    type="string",
                    required=True,
                ),
                "minDuration": ToolParameter(
                    description="Minimum duration in seconds to filter queries",
                    type="string",
                    required=False,
                ),
                "blockedOnly": ToolParameter(
                    description="Set to 'true' to show only blocked queries",
                    type="string",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        instance_id = params.get("instanceId")
        if not instance_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing required parameter: instanceId",
                params=params,
            )

        query_params: Dict = {"instanceId": instance_id}
        if params.get("minDuration"):
            query_params["minDuration"] = params["minDuration"]
        if params.get("blockedOnly"):
            query_params["blockedOnly"] = params["blockedOnly"]

        try:
            data = self._toolset.client.get("/api/queries/running", params=query_params)
            if not data.get("data"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=f"No running queries on instance {instance_id}.",
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch running queries from {self._toolset.dbdash_config.api_url}/api/queries/running: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Running Queries on instance {params.get('instanceId', '?')}"


class GetBlockingQueries(Tool):
    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_get_blocking_queries",
            description=(
                "Get blocking query chains for a SQL Server instance. Returns head blockers, "
                "blocked sessions, wait types, durations, and SQL text. Also includes a summary "
                "of blocking patterns and snapshot history."
            ),
            parameters={
                "instanceId": ToolParameter(
                    description="The instance ID. Optional - omit to see all instances.",
                    type="string",
                    required=False,
                ),
                "from": ToolParameter(
                    description="Start datetime in ISO 8601 format. Defaults to 24 hours ago.",
                    type="string",
                    required=False,
                ),
                "to": ToolParameter(
                    description="End datetime in ISO 8601 format. Defaults to now.",
                    type="string",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        default_from, default_to = _default_time_range()
        query_params: Dict = {
            "from": params.get("from", default_from),
            "to": params.get("to", default_to),
        }
        if params.get("instanceId"):
            query_params["instanceId"] = params["instanceId"]

        try:
            data = self._toolset.client.get("/api/queries/blocking", params=query_params)
            if not data.get("data") and not data.get("summary"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=f"No blocking queries found for params {query_params}.",
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch blocking queries from {self._toolset.dbdash_config.api_url}/api/queries/blocking: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Blocking Queries"


class GetQueryStoreTop(Tool):
    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_get_query_store_top",
            description=(
                "Get top resource-consuming queries from Query Store for a SQL Server instance. "
                "Can sort by CPU, duration, execution count, memory, logical I/O, or physical I/O."
            ),
            parameters={
                "instanceId": ToolParameter(
                    description="The instance ID (required)",
                    type="string",
                    required=True,
                ),
                "databaseId": ToolParameter(
                    description="Filter to a specific database ID",
                    type="string",
                    required=False,
                ),
                "metric": ToolParameter(
                    description="Sort metric: cpu, duration, execution_count, memory, logical_io, physical_io",
                    type="string",
                    required=False,
                    enum=["cpu", "duration", "execution_count", "memory", "logical_io", "physical_io"],
                ),
                "top": ToolParameter(
                    description="Number of top queries to return (default: 100)",
                    type="string",
                    required=False,
                ),
                "from": ToolParameter(
                    description="Start datetime in ISO 8601 format. Defaults to 24 hours ago.",
                    type="string",
                    required=False,
                ),
                "to": ToolParameter(
                    description="End datetime in ISO 8601 format. Defaults to now.",
                    type="string",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        instance_id = params.get("instanceId")
        if not instance_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing required parameter: instanceId",
                params=params,
            )

        default_from, default_to = _default_time_range()
        query_params: Dict = {
            "instanceId": instance_id,
            "from": params.get("from", default_from),
            "to": params.get("to", default_to),
        }
        if params.get("databaseId"):
            query_params["databaseId"] = params["databaseId"]
        if params.get("metric"):
            query_params["metric"] = params["metric"]
        if params.get("top"):
            query_params["top"] = params["top"]

        try:
            data = self._toolset.client.get("/api/queries/query-store", params=query_params)
            if not data.get("data"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=f"No Query Store data for instance {instance_id}.",
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch Query Store data from {self._toolset.dbdash_config.api_url}/api/queries/query-store: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        metric = params.get("metric", "cpu")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Top Queries by {metric}"
