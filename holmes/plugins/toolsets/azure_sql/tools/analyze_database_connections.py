import logging
from typing import Any, Dict, Tuple
from datetime import datetime, timezone

from holmes.core.tools import (
    StructuredToolResult,
    ToolInvokeContext,
    ToolParameter,
    StructuredToolResultStatus,
)
from holmes.plugins.toolsets.azure_sql.azure_base_toolset import (
    BaseAzureSQLTool,
    BaseAzureSQLToolset,
    AzureSQLDatabaseConfig,
)
from holmes.plugins.toolsets.azure_sql.apis.connection_monitoring_api import (
    ConnectionMonitoringAPI,
)
from holmes.plugins.toolsets.azure_sql.apis.azure_sql_api import AzureSQLAPIClient
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class AnalyzeDatabaseConnections(BaseAzureSQLTool):
    def __init__(self, toolset: "BaseAzureSQLToolset"):
        super().__init__(
            name="analyze_database_connections",
            description="Analyzes database connection patterns, active connections, and connection pool utilization. Use this to investigate connection-related issues, blocking sessions, and connection pool exhaustion.",
            parameters={
                "hours_back": ToolParameter(
                    description="Time window for metrics analysis in hours. Use 2 for recent activity, 24+ for trend analysis (default: 2)",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _build_connection_report(
        self, db_config: AzureSQLDatabaseConfig, connection_data: Dict, hours_back: int
    ) -> str:
        """Build the formatted connection report from gathered data."""
        report_sections = []

        # Header
        report_sections.append("# Azure SQL Database Connection Report")
        report_sections.append(f"**Database:** {db_config.database_name}")
        report_sections.append(f"**Server:** {db_config.server_name}")
        report_sections.append(f"**Analysis Period:** Last {hours_back} hours")
        report_sections.append(
            f"**Generated:** {datetime.now(timezone.utc).isoformat()}"
        )
        report_sections.append("")

        # Connection Summary
        report_sections.append("## Connection Summary")
        summary = connection_data.get("summary", {})
        if "error" in summary:
            report_sections.append(
                f"⚠️ **Error retrieving connection summary:** {summary['error']}"
            )
        else:
            total_conn = summary.get("total_connections", 0)
            active_conn = summary.get("active_connections", 0)
            idle_conn = summary.get("idle_connections", 0)
            blocked_conn = summary.get("blocked_connections", 0)

            report_sections.append(f"- **Total Connections**: {total_conn}")
            report_sections.append(f"- **Active Connections**: {active_conn}")
            report_sections.append(f"- **Idle Connections**: {idle_conn}")
            if blocked_conn > 0:
                report_sections.append(f"- **🚨 Blocked Connections**: {blocked_conn}")
            else:
                report_sections.append(f"- **Blocked Connections**: {blocked_conn}")
            report_sections.append(
                f"- **Unique Users**: {summary.get('unique_users', 0)}"
            )
            report_sections.append(
                f"- **Unique Hosts**: {summary.get('unique_hosts', 0)}"
            )
        report_sections.append("")

        # Connection Pool Statistics
        report_sections.append("## Connection Pool Statistics")
        pool_stats = connection_data.get("pool_stats", {})
        if "error" in pool_stats:
            report_sections.append(
                f"⚠️ **Error retrieving pool stats:** {pool_stats['error']}"
            )
        else:
            for metric_name, metric_data in pool_stats.items():
                if isinstance(metric_data, dict) and "value" in metric_data:
                    value = metric_data["value"]
                    unit = metric_data.get("unit", "")
                    report_sections.append(f"- **{metric_name}**: {value:,} {unit}")
        report_sections.append("")

        # Active Connections Detail
        report_sections.append("## Active Connections Detail")
        active_connections = connection_data.get("active_connections", [])
        if active_connections:
            active_count = len(
                [
                    conn
                    for conn in active_connections
                    if conn.get("connection_status") == "Active"
                ]
            )
            report_sections.append(f"**{active_count} active connections found:**")
            report_sections.append("")

            for i, conn in enumerate(active_connections[:10], 1):  # Show top 10
                if conn.get("connection_status") == "Active":
                    login_name = conn.get("login_name", "Unknown")
                    host_name = conn.get("host_name", "Unknown")
                    status = conn.get("status", "Unknown")
                    cpu_time = conn.get("cpu_time", 0)
                    wait_type = conn.get("wait_type", "")
                    blocking_session = conn.get("blocking_session_id", 0)

                    report_sections.append(f"### Connection #{i}")
                    report_sections.append(f"- **User**: {login_name}@{host_name}")
                    report_sections.append(f"- **Status**: {status}")
                    report_sections.append(f"- **CPU Time**: {cpu_time:,} ms")
                    if wait_type:
                        report_sections.append(f"- **Wait Type**: {wait_type}")
                    if blocking_session and blocking_session > 0:
                        report_sections.append(
                            f"- **🚨 Blocked by Session**: {blocking_session}"
                        )
                    report_sections.append("")
        else:
            report_sections.append("No active connections found")

        # Azure Monitor Metrics (if available)
        report_sections.append("## Azure Monitor Connection Metrics")
        metrics = connection_data.get("metrics", {})
        if "error" in metrics:
            report_sections.append(f"⚠️ **Metrics unavailable:** {metrics['error']}")
        else:
            for metric_name, metric_data in metrics.items():
                if metric_data:
                    recent_values = metric_data[-5:]  # Last 5 data points
                    if recent_values:
                        avg_value = sum(
                            point.get("average", 0) or 0 for point in recent_values
                        ) / len(recent_values)
                        max_value = max(
                            point.get("maximum", 0) or 0 for point in recent_values
                        )
                        report_sections.append(
                            f"- **{metric_name}**: Avg {avg_value:.1f}, Max {max_value:.1f}"
                        )

            if not any(metrics.values()):
                report_sections.append("No recent metric data available")

        return "\n".join(report_sections)

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            hours_back = params.get("hours_back", 2)

            db_config = self.toolset.database_config()

            # Create connection monitoring API client
            api_client = self.toolset.api_client()
            connection_api = ConnectionMonitoringAPI(
                credential=api_client.credential,
                subscription_id=db_config.subscription_id,
            )

            # Gather connection data
            connection_data: Dict[str, Any] = {}

            # Get connection summary
            connection_data["summary"] = connection_api.get_connection_summary(
                db_config.server_name, db_config.database_name
            )

            # Get active connections
            connection_data["active_connections"] = (
                connection_api.get_active_connections(
                    db_config.server_name, db_config.database_name
                )
            )

            # Get connection pool stats
            connection_data["pool_stats"] = connection_api.get_connection_pool_stats(
                db_config.server_name, db_config.database_name
            )

            # Get Azure Monitor metrics
            connection_data["metrics"] = connection_api.get_connection_metrics(
                db_config.resource_group,
                db_config.server_name,
                db_config.database_name,
                hours_back,
            )

            # Build the formatted report
            report_text = self._build_connection_report(
                db_config, connection_data, hours_back
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=report_text,
                params=params,
            )
        except Exception as e:
            error_msg = f"Failed to generate connection report: {str(e)}"
            logging.error(error_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        db_config = self.toolset.database_config()
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Analyze Database Connections ({db_config.server_name}/{db_config.database_name})"

    @staticmethod
    def validate_config(
        api_client: AzureSQLAPIClient, database_config: AzureSQLDatabaseConfig
    ) -> Tuple[bool, str]:
        error = ""

        try:
            # Test database advisors API access
            api_client.get_database_advisors(
                database_config.subscription_id,
                database_config.resource_group,
                database_config.server_name,
                database_config.database_name,
            )
        except Exception as e:
            error_msg = str(e)
            if (
                "authorization" in error_msg.lower()
                or "permission" in error_msg.lower()
            ):
                error = f"Database management API access denied: {error_msg}"
            else:
                error = f"Database management API connection failed: {error_msg}"

        if error:
            return False, error
        return True, ""
