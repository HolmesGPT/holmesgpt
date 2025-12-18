import logging
from typing import Dict
from datetime import datetime, timezone

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
)
from holmes.plugins.toolsets.azure_sql.azure_base_toolset import (
    BaseAzureSQLTool,
    BaseAzureSQLToolset,
    AzureSQLDatabaseConfig,
)
from holmes.plugins.toolsets.azure_sql.apis.azure_sql_api import AzureSQLAPIClient
from holmes.plugins.toolsets.azure_sql.apis.alert_monitoring_api import (
    AlertMonitoringAPI,
)
from typing import Tuple

from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class GetActiveAlerts(BaseAzureSQLTool):
    def __init__(self, toolset: "BaseAzureSQLToolset"):
        super().__init__(
            name="get_active_alerts",
            description="Retrieves currently active Azure Monitor alerts for the SQL database and server. Use this to identify ongoing issues, performance problems, and service health alerts that need immediate attention.",
            parameters={},
            toolset=toolset,
        )

    def _build_alerts_report(
        self, db_config: AzureSQLDatabaseConfig, alerts_data: Dict, alert_type: str
    ) -> str:
        """Build the formatted alerts report from gathered data."""
        report_sections = []

        # Header
        report_sections.append(
            f"# Azure SQL Database {alert_type.title()} Alerts Report"
        )
        report_sections.append(f"**Database:** {db_config.database_name}")
        report_sections.append(f"**Server:** {db_config.server_name}")
        report_sections.append(f"**Resource Group:** {db_config.resource_group}")
        report_sections.append(
            f"**Generated:** {datetime.now(timezone.utc).isoformat()}"
        )
        report_sections.append("")

        # Summary
        total_alerts = alerts_data.get("total_count", 0)
        active_alerts = alerts_data.get("active_alerts", [])

        report_sections.append("## Summary")
        if total_alerts == 0:
            report_sections.append("✅ **No active alerts** - System appears healthy")
        else:
            severity_counts: dict = {}
            scope_counts: dict = {}
            for alert in active_alerts:
                severity = alert.get("severity", "Unknown")
                scope = alert.get("scope", "Unknown")
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
                scope_counts[scope] = scope_counts.get(scope, 0) + 1

            report_sections.append(f"🚨 **{total_alerts} active alerts detected**")

            # Severity breakdown
            if severity_counts:
                report_sections.append("### Severity Breakdown:")
                for severity, count in sorted(severity_counts.items()):
                    icon = (
                        "🔴"
                        if severity in ["Sev0", "Critical"]
                        else "🟡"
                        if severity in ["Sev1", "Error"]
                        else "🟢"
                    )
                    report_sections.append(f"- **{severity}**: {count} alerts {icon}")

            # Scope breakdown
            if scope_counts:
                report_sections.append("### Scope Breakdown:")
                for scope, count in sorted(scope_counts.items()):
                    report_sections.append(f"- **{scope.title()}**: {count} alerts")

        report_sections.append("")

        # Alert Details
        if active_alerts:
            report_sections.append("## Active Alerts Details")

            # Sort by severity (most critical first)
            severity_order = {
                "Sev0": 0,
                "Critical": 0,
                "Sev1": 1,
                "Error": 1,
                "Sev2": 2,
                "Warning": 2,
                "Sev3": 3,
                "Informational": 3,
            }
            active_alerts.sort(
                key=lambda x: severity_order.get(x.get("severity", "Unknown"), 99)
            )

            for i, alert in enumerate(active_alerts, 1):
                alert_id = alert.get("id", "Unknown")
                name = alert.get("name", "Unknown Alert")
                description = alert.get("description", "No description available")
                severity = alert.get("severity", "Unknown")
                state = alert.get("state", "Unknown")
                fired_time = alert.get("fired_time", "Unknown")
                scope = alert.get("scope", "Unknown")
                resource_type = alert.get("resource_type", "Unknown")

                # Format severity with icon
                severity_icon = (
                    "🔴"
                    if severity in ["Sev0", "Critical"]
                    else "🟡"
                    if severity in ["Sev1", "Error"]
                    else "🟢"
                )

                report_sections.append(f"### Alert #{i}: {name}")
                report_sections.append(f"- **Severity**: {severity} {severity_icon}")
                report_sections.append(f"- **State**: {state}")
                report_sections.append(f"- **Scope**: {scope.title()}")
                report_sections.append(f"- **Resource Type**: {resource_type}")
                report_sections.append(f"- **Fired Time**: {fired_time}")
                report_sections.append(f"- **Alert ID**: {alert_id}")
                report_sections.append(f"- **Description**: {description}")
                report_sections.append("")

        # Resource Information
        report_sections.append("## Resource Information")
        report_sections.append(
            f"- **Database Resource ID**: {alerts_data.get('database_resource_id', 'N/A')}"
        )
        report_sections.append(
            f"- **Server Resource ID**: {alerts_data.get('server_resource_id', 'N/A')}"
        )

        # Metadata
        method = alerts_data.get("method")
        if method:
            report_sections.append(f"- **Data Source**: {method}")

        return "\n".join(report_sections)

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            db_config = self.toolset.database_config()
            api_client = self.toolset.api_client()

            # Create alert monitoring API client
            alert_api = AlertMonitoringAPI(
                credential=api_client.credential,
                subscription_id=db_config.subscription_id,
            )

            # Get active alerts
            alerts_data = alert_api.get_active_alerts(
                db_config.resource_group,
                db_config.server_name,
                db_config.database_name,
            )

            # Check for errors
            if "error" in alerts_data:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=alerts_data["error"],
                    params=params,
                )

            # Build the formatted report
            report_text = self._build_alerts_report(db_config, alerts_data, "active")

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=report_text,
                params=params,
            )
        except Exception as e:
            error_msg = f"Failed to retrieve active alerts: {str(e)}"
            logging.error(error_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        db_config = self.toolset.database_config()
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Active Alerts ({db_config.server_name}/{db_config.database_name})"

    @staticmethod
    def validate_config(
        api_client: AzureSQLAPIClient, database_config: AzureSQLDatabaseConfig
    ) -> Tuple[bool, str]:
        errors = []

        try:
            # Test alert monitoring API access
            alert_api = AlertMonitoringAPI(
                credential=api_client.credential,
                subscription_id=database_config.subscription_id,
            )

            # Test getting active alerts
            alerts_data = alert_api.get_active_alerts(
                database_config.resource_group,
                database_config.server_name,
                database_config.database_name,
            )

            if "error" in alerts_data:
                error_msg = alerts_data["error"]
                if (
                    "authorization" in error_msg.lower()
                    or "permission" in error_msg.lower()
                ):
                    errors.append(f"Alert monitoring access denied: {error_msg}")
                else:
                    errors.append(f"Alert monitoring connection failed: {error_msg}")

        except Exception as e:
            error_msg = str(e)
            if (
                "authorization" in error_msg.lower()
                or "permission" in error_msg.lower()
            ):
                errors.append(f"Alert monitoring API access denied: {error_msg}")
            else:
                errors.append(f"Alert monitoring API connection failed: {error_msg}")

        if errors:
            return False, "\n".join(errors)
        return True, ""
