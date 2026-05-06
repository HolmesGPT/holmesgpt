import datetime
from typing import TYPE_CHECKING, Any, Dict

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

if TYPE_CHECKING:
    from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

DEFAULT_HOURS = 24


def _default_time_range() -> tuple[str, str]:
    """Return (from, to) ISO strings for the last 24 hours."""
    now = datetime.datetime.now(datetime.timezone.utc)
    from_time = now - datetime.timedelta(hours=DEFAULT_HOURS)
    return from_time.isoformat(), now.isoformat()


def _build_time_params(params: dict) -> Dict[str, Any]:
    """Build query params with instanceId and time range."""
    instance_id = params.get("instanceId")
    if not instance_id:
        raise ValueError("Missing required parameter: instanceId")

    from_time = params.get("from")
    to_time = params.get("to")
    if not from_time or not to_time:
        default_from, default_to = _default_time_range()
        from_time = from_time or default_from
        to_time = to_time or default_to

    return {
        "instanceId": instance_id,
        "from": from_time,
        "to": to_time,
    }


_TIME_RANGE_PARAMS = {
    "instanceId": ToolParameter(
        description="The instance ID (from dbdash_list_instances)",
        type="string",
        required=True,
    ),
    "from": ToolParameter(
        description="Start datetime in ISO 8601 format (e.g., 2026-03-30T00:00:00Z). Defaults to 24 hours ago.",
        type="string",
        required=False,
    ),
    "to": ToolParameter(
        description="End datetime in ISO 8601 format. Defaults to now.",
        type="string",
        required=False,
    ),
}


class GetCpuMetrics(Tool):
    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_get_cpu_metrics",
            description=(
                "Get CPU usage over time for a SQL Server instance. Returns SQL process CPU, "
                "other CPU, and max CPU values, plus a histogram of CPU usage distribution."
            ),
            parameters=dict(_TIME_RANGE_PARAMS),
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            query_params = _build_time_params(params)
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

        try:
            data = self._toolset.client.get("/api/performance/cpu", params=query_params)
            if not data.get("data"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=(
                        f"No CPU data for instance {query_params['instanceId']} "
                        f"between {query_params['from']} and {query_params['to']}."
                    ),
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
                error=(
                    f"Failed to fetch CPU metrics from "
                    f"{self._toolset.dbdash_config.api_url}/api/performance/cpu "
                    f"with params {query_params}: {e}"
                ),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: CPU Metrics for instance {params.get('instanceId', '?')}"


class GetMemoryMetrics(Tool):
    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_get_memory_metrics",
            description=(
                "Get memory usage over time for a SQL Server instance. Returns total server memory, "
                "target memory, free memory, and memory clerk breakdown."
            ),
            parameters=dict(_TIME_RANGE_PARAMS),
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            query_params = _build_time_params(params)
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

        try:
            data = self._toolset.client.get("/api/performance/memory", params=query_params)
            if not data.get("data"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=(
                        f"No memory data for instance {query_params['instanceId']} "
                        f"between {query_params['from']} and {query_params['to']}."
                    ),
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
                error=(
                    f"Failed to fetch memory metrics from "
                    f"{self._toolset.dbdash_config.api_url}/api/performance/memory "
                    f"with params {query_params}: {e}"
                ),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Memory Metrics for instance {params.get('instanceId', '?')}"


class GetWaitStats(Tool):
    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_get_wait_stats",
            description=(
                "Get wait statistics for a SQL Server instance. Returns time series of wait types "
                "and a summary with total/signal wait times. Critical for identifying bottleneck types "
                "(I/O, locks, memory, CPU, network)."
            ),
            parameters=dict(_TIME_RANGE_PARAMS),
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            query_params = _build_time_params(params)
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

        try:
            data = self._toolset.client.get("/api/performance/waits", params=query_params)
            if not data.get("data") and not data.get("summary"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=(
                        f"No wait stats for instance {query_params['instanceId']} "
                        f"between {query_params['from']} and {query_params['to']}."
                    ),
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
                error=(
                    f"Failed to fetch wait stats from "
                    f"{self._toolset.dbdash_config.api_url}/api/performance/waits "
                    f"with params {query_params}: {e}"
                ),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Wait Stats for instance {params.get('instanceId', '?')}"


class GetIoStats(Tool):
    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_get_io_stats",
            description=(
                "Get I/O statistics for a SQL Server instance. Returns time series of read/write "
                "latency, throughput (MB/s), and IOPS, plus per-database and per-filegroup summaries."
            ),
            parameters=dict(_TIME_RANGE_PARAMS),
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            query_params = _build_time_params(params)
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

        try:
            data = self._toolset.client.get("/api/performance/io", params=query_params)
            if not data.get("timeSeries"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=(
                        f"No I/O data for instance {query_params['instanceId']} "
                        f"between {query_params['from']} and {query_params['to']}."
                    ),
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
                error=(
                    f"Failed to fetch I/O stats from "
                    f"{self._toolset.dbdash_config.api_url}/api/performance/io "
                    f"with params {query_params}: {e}"
                ),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: I/O Stats for instance {params.get('instanceId', '?')}"
