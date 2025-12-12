import logging
from typing import Dict, Tuple
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
from holmes.plugins.toolsets.azure_sql.apis.azure_sql_api import AzureSQLAPIClient
from holmes.plugins.toolsets.azure_sql.apis.connection_failure_api import (
    ConnectionFailureAPI,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class AnalyzeConnectionFailures(BaseAzureSQLTool):
    def __init__(self, toolset: "BaseAzureSQLToolset"):
        super().__init__(
            name="analyze_connection_failures",
            description="Analyzes connection failures, firewall blocks, and connection patterns for Azure SQL Database. Use this to investigate connection issues, authentication problems, and network connectivity problems.",
            parameters={
                "hours_back": ToolParameter(
                    description="Number of hours to look back for connection failure analysis (default: 24, max: 168)",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _build_connection_failures_report(
        self, db_config: AzureSQLDatabaseConfig, analysis_data: Dict, hours_back: int
    ) -> str:
        """Build the formatted connection failures report from gathered data."""
        report_sections = []

        # Header
        report_sections.append("# Azure SQL Database Connection Failures Analysis")
        report_sections.append(f"**Database:** {db_config.database_name}")
        report_sections.append(f"**Server:** {db_config.server_name}")
        report_sections.append(f"**Resource Group:** {db_config.resource_group}")
        report_sections.append(f"**Analysis Period:** {hours_back} hours")
        report_sections.append(
            f"**Generated:** {datetime.now(timezone.utc).isoformat()}"
        )
        report_sections.append("")

        # Summary
        analysis = analysis_data.get("analysis", {})
        summary = analysis.get("summary", {})
        issues = analysis.get("issues_detected", [])
        recommendations = analysis.get("recommendations", [])

        report_sections.append("## Executive Summary")
        if summary.get("status") == "healthy":
            report_sections.append(
                "✅ **Status: HEALTHY** - No significant connection issues detected"
            )
        else:
            report_sections.append(
                "⚠️ **Status: ISSUES DETECTED** - Connection problems identified"
            )

        if summary.get("message"):
            report_sections.append(f"- {summary['message']}")
        report_sections.append("")

        # Issues Detected
        if issues:
            report_sections.append("## Issues Detected")
            for issue in issues:
                report_sections.append(f"- {issue}")
            report_sections.append("")

        # Metrics Analysis
        metrics_analysis = analysis.get("metrics_analysis", {})
        if metrics_analysis:
            report_sections.append("## Connection Metrics Analysis")

            # Connection failures
            if "connection_failures" in metrics_analysis:
                failures = metrics_analysis["connection_failures"]
                report_sections.append("### Connection Failures")
                report_sections.append(
                    f"- **Total Failed Connections:** {int(failures.get('total_failed_connections', 0))}"
                )
                report_sections.append(
                    f"- **Peak Failures (1 hour):** {int(failures.get('max_failures_per_hour', 0))}"
                )
                report_sections.append(
                    f"- **Trend:** {failures.get('failure_trend', 'Unknown').title()}"
                )
                report_sections.append("")

            # Successful connections
            if "successful_connections" in metrics_analysis:
                successful = metrics_analysis["successful_connections"]
                report_sections.append("### Successful Connections")
                report_sections.append(
                    f"- **Total Successful Connections:** {int(successful.get('total_successful_connections', 0))}"
                )
                report_sections.append("")

            # Failure rate
            if "failure_rate_percent" in metrics_analysis:
                failure_rate = metrics_analysis["failure_rate_percent"]
                status_icon = (
                    "🔴" if failure_rate > 5 else "🟡" if failure_rate > 1 else "🟢"
                )
                report_sections.append("### Overall Connection Health")
                report_sections.append(
                    f"- **Failure Rate:** {failure_rate}% {status_icon}"
                )
                report_sections.append("")

        # Activity Log Events
        activity_data = analysis_data.get("activity_events", {})
        if activity_data.get("events"):
            report_sections.append("## Activity Log Events")
            report_sections.append(
                f"- **Total Events:** {activity_data.get('total_events', 0)}"
            )
            report_sections.append(
                f"- **Connection-Related Events:** {activity_data.get('connection_related_events', 0)}"
            )
            report_sections.append(
                f"- **Error Events:** {activity_data.get('error_events', 0)}"
            )
            report_sections.append(
                f"- **Warning Events:** {activity_data.get('warning_events', 0)}"
            )

            # Show recent critical events
            critical_events = [
                e
                for e in activity_data["events"][:10]
                if e["level"] in ["Error", "Critical"]
            ]

            if critical_events:
                report_sections.append("")
                report_sections.append("### Recent Critical Events")
                for event in critical_events:
                    report_sections.append(
                        f"- **{event['timestamp']}** - {event['operation_name']}"
                    )
                    report_sections.append(f"  - Level: {event['level']}")
                    report_sections.append(f"  - Status: {event['status']}")
                    if (
                        event.get("description")
                        and event["description"] != "No description"
                    ):
                        report_sections.append(
                            f"  - Description: {event['description']}"
                        )
                    report_sections.append("")

        # Detailed Metrics Data
        connection_metrics = analysis_data.get("connection_metrics", {})
        if connection_metrics:
            report_sections.append("## Detailed Metrics")

            for metric_name, metric_data in connection_metrics.items():
                if metric_data.get("values") and not metric_data.get("error"):
                    values = metric_data["values"]
                    if values:
                        total_value = sum(dp.get("total", 0) or 0 for dp in values)
                        max_value = max(
                            (dp.get("maximum", 0) or 0 for dp in values), default=0
                        )
                        avg_value = (
                            sum(dp.get("average", 0) or 0 for dp in values)
                            / len(values)
                            if values
                            else 0
                        )

                        report_sections.append(
                            f"### {metric_name.replace('_', ' ').title()}"
                        )
                        report_sections.append(f"- **Total:** {int(total_value)}")
                        report_sections.append(f"- **Peak (1 hour):** {int(max_value)}")
                        report_sections.append(f"- **Average:** {avg_value:.1f}")
                        report_sections.append(f"- **Data Points:** {len(values)}")
                        report_sections.append("")

        # Recommendations
        if recommendations:
            report_sections.append("## Recommendations")
            for rec in recommendations:
                report_sections.append(f"- {rec}")
            report_sections.append("")

        # Resource Information
        report_sections.append("## Resource Information")
        report_sections.append(
            f"- **Database Resource ID:** {analysis_data.get('database_resource_id', 'N/A')}"
        )
        report_sections.append(
            f"- **Server Resource ID:** {analysis_data.get('server_resource_id', 'N/A')}"
        )

        time_range = analysis_data.get("time_range", {})
        if time_range:
            report_sections.append(
                f"- **Analysis Start:** {time_range.get('start', 'N/A')}"
            )
            report_sections.append(
                f"- **Analysis End:** {time_range.get('end', 'N/A')}"
            )

        return "\n".join(report_sections)

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            # Get configuration
            db_config = self.toolset.database_config()
            api_client = self.toolset.api_client()

            # Parse parameters
            hours_back = params.get("hours_back", 24)
            hours_back = max(1, min(hours_back, 168))  # Limit between 1 and 168 hours

            # Create connection failure API client
            connection_api = ConnectionFailureAPI(
                credential=api_client.credential,
                subscription_id=db_config.subscription_id,
            )

            # Analyze connection failures
            analysis_data = connection_api.analyze_connection_failures(
                db_config.resource_group,
                db_config.server_name,
                db_config.database_name,
                hours_back,
            )

            # Check for errors
            if "error" in analysis_data:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=analysis_data["error"],
                    params=params,
                )

            # Build the formatted report
            report_text = self._build_connection_failures_report(
                db_config, analysis_data, hours_back
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=report_text,
                params=params,
            )

        except Exception as e:
            logging.error(
                f"Error in analyze_connection_failures: {str(e)}", exc_info=True
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to analyze connection failures: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        db_config = self.toolset.database_config()
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Analyze Connection Failures ({db_config.server_name}/{db_config.database_name})"

    @staticmethod
    def validate_config(
        api_client: AzureSQLAPIClient, database_config: AzureSQLDatabaseConfig
    ) -> Tuple[bool, str]:
        errors = []

        try:
            # Test connection failure API access
            connection_api = ConnectionFailureAPI(
                credential=api_client.credential,
                subscription_id=database_config.subscription_id,
            )

            # Test getting connection metrics (try a minimal request)
            test_analysis = connection_api.analyze_connection_failures(
                database_config.resource_group,
                database_config.server_name,
                database_config.database_name,
                hours_back=1,  # Minimal test
            )

            if "error" in test_analysis:
                error_msg = test_analysis["error"]
                if (
                    "authorization" in error_msg.lower()
                    or "permission" in error_msg.lower()
                ):
                    errors.append(
                        f"Connection failure monitoring access denied: {error_msg}"
                    )
                else:
                    errors.append(
                        f"Connection failure monitoring API failed: {error_msg}"
                    )

        except Exception as e:
            error_msg = str(e)
            if (
                "authorization" in error_msg.lower()
                or "permission" in error_msg.lower()
            ):
                errors.append(
                    f"Connection failure monitoring API access denied: {error_msg}"
                )
            else:
                errors.append(
                    f"Connection failure monitoring API connection failed: {error_msg}"
                )

        if errors:
            return False, "\n".join(errors)
        return True, ""
