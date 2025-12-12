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
from typing import Tuple

from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class AnalyzeDatabaseHealthStatus(BaseAzureSQLTool):
    def __init__(self, toolset: "BaseAzureSQLToolset"):
        super().__init__(
            name="analyze_database_health_status",
            description="Analyzes the overall health status of an Azure SQL database including active operations, resource usage alerts, and system status. Use this first to get a high-level view of database health.",
            parameters={},
            toolset=toolset,
        )

    def _gather_health_data(
        self, db_config: AzureSQLDatabaseConfig, client: AzureSQLAPIClient
    ) -> Dict:
        """Gather health-related data from Azure SQL API."""
        health_data = {
            "database_info": {
                "name": db_config.database_name,
                "server": db_config.server_name,
                "resource_group": db_config.resource_group,
                "subscription_id": db_config.subscription_id,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            operations = client.get_database_operations(
                db_config.subscription_id,
                db_config.resource_group,
                db_config.server_name,
                db_config.database_name,
            )
            health_data["operations"] = operations.get("value", [])
        except Exception as e:
            health_data["operations_error"] = str(e)

        try:
            usages = client.get_database_usages(
                db_config.subscription_id,
                db_config.resource_group,
                db_config.server_name,
                db_config.database_name,
            )
            health_data["resource_usage"] = usages.get("value", [])
        except Exception as e:
            health_data["usage_error"] = str(e)

        return health_data

    def _build_health_report(
        self, health_data: Dict, db_config: AzureSQLDatabaseConfig
    ) -> str:
        """Build the formatted health report from gathered data."""
        report_sections = []

        # Database Overview Section
        report_sections.append("# Azure SQL Database Health Report")
        report_sections.append(f"**Database:** {db_config.database_name}")
        report_sections.append(f"**Server:** {db_config.server_name}")
        report_sections.append(f"**Generated:** {health_data['timestamp']}")
        report_sections.append("")

        # Operations Status Section
        report_sections.append("## Operations Status")
        if "operations_error" in health_data:
            report_sections.append(
                f"⚠️ **Error retrieving operations:** {health_data['operations_error']}"
            )
        else:
            operations = health_data.get("operations", [])
            if operations:
                report_sections.append(f"**Active Operations:** {len(operations)}")
                for op in operations[:5]:  # Show first 5 operations
                    status = op.get("properties", {}).get("state", "Unknown")
                    op_type = op.get("properties", {}).get("operation", "Unknown")
                    start_time = op.get("properties", {}).get("startTime", "Unknown")
                    report_sections.append(
                        f"- **{op_type}**: {status} (Started: {start_time})"
                    )
            else:
                report_sections.append("✅ **No active operations**")
        report_sections.append("")

        # Resource Usage Section
        report_sections.append("## Resource Usage")
        if "usage_error" in health_data:
            report_sections.append(
                f"⚠️ **Error retrieving usage data:** {health_data['usage_error']}"
            )
        else:
            usages = health_data.get("resource_usage", [])
            if usages:
                for usage in usages:
                    name = usage.get(
                        "display_name", usage.get("displayName", "Unknown Metric")
                    )
                    current = usage.get("current_value", usage.get("currentValue", 0))
                    limit = usage.get("limit", 0)
                    unit = usage.get("unit", "")

                    if limit > 0:
                        percentage = (current / limit) * 100
                        status_icon = (
                            "🔴"
                            if percentage > 90
                            else "🟡"
                            if percentage > 70
                            else "🟢"
                        )
                        report_sections.append(
                            f"- **{name}**: {status_icon} {current:,} / {limit:,} {unit} ({percentage:.1f}%)"
                        )
                    else:
                        report_sections.append(f"- **{name}**: {current:,} {unit}")
            else:
                report_sections.append("No usage data available")

        return "\n".join(report_sections)

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            db_config = self.toolset.database_config()
            client = self.toolset.api_client()

            # Gather health-related data
            health_data = self._gather_health_data(db_config, client)

            # Build the formatted report
            report_text = self._build_health_report(health_data, db_config)

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=report_text,
                params=params,
            )
        except Exception as e:
            error_msg = f"Failed to generate health report: {str(e)}"
            logging.error(error_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        db_config = self.toolset.database_config()
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Analyze Health Status ({db_config.server_name}/{db_config.database_name})"

    @staticmethod
    def validate_config(
        api_client: AzureSQLAPIClient, database_config: AzureSQLDatabaseConfig
    ) -> Tuple[bool, str]:
        errors = []

        try:
            # Test database operations API access
            api_client.get_database_operations(
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
                errors.append(f"Database operations access denied: {error_msg}")
            else:
                errors.append(f"Database operations connection failed: {error_msg}")

        try:
            # Test database usages API access
            api_client.get_database_usages(
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
                errors.append(f"Database usage metrics access denied: {error_msg}")
            else:
                errors.append(f"Database usage metrics connection failed: {error_msg}")

        if errors:
            return False, "\n".join(errors)
        return True, ""
