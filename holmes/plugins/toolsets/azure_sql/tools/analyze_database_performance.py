import logging
from typing import Any, Dict, List, Tuple, cast
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
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class AnalyzeDatabasePerformance(BaseAzureSQLTool):
    def __init__(self, toolset: "BaseAzureSQLToolset"):
        super().__init__(
            name="analyze_database_performance",
            description="Analyzes database performance including automatic tuning status, performance advisors, and active recommendations. Essential for identifying performance optimization opportunities.",
            parameters={},
            toolset=toolset,
        )

    def _gather_performance_data(
        self, db_config: AzureSQLDatabaseConfig, client: AzureSQLAPIClient
    ) -> Dict:
        """Gather performance-related data from Azure SQL API."""
        performance_data = {
            "database_info": {
                "name": db_config.database_name,
                "server": db_config.server_name,
                "resource_group": db_config.resource_group,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            advisors = client.get_database_advisors(
                db_config.subscription_id,
                db_config.resource_group,
                db_config.server_name,
                db_config.database_name,
            )
            performance_data["advisors"] = advisors.get("value", [])
        except Exception as e:
            performance_data["advisors_error"] = str(e)

        try:
            auto_tuning = client.get_database_automatic_tuning(
                db_config.subscription_id,
                db_config.resource_group,
                db_config.server_name,
                db_config.database_name,
            )
            performance_data["automatic_tuning"] = auto_tuning
        except Exception as e:
            performance_data["auto_tuning_error"] = str(e)

        # Get recommendations for each advisor
        recommendations_list: List[Dict[str, Any]] = []
        performance_data["recommendations"] = cast(Any, recommendations_list)
        if "advisors" in performance_data:
            for advisor in performance_data["advisors"]:
                if isinstance(advisor, dict):
                    advisor_name = advisor.get("name", "")
                else:
                    advisor_name = str(advisor)
                try:
                    recommendations = client.get_database_recommended_actions(
                        db_config.subscription_id,
                        db_config.resource_group,
                        db_config.server_name,
                        db_config.database_name,
                        advisor_name,
                    )
                    recommendations_list.extend(recommendations.get("value", []))
                except Exception as e:
                    logging.warning(
                        f"Failed to get recommendations for advisor {advisor_name}: {e}"
                    )

        return performance_data

    def _build_performance_report(
        self, performance_data: Dict, db_config: AzureSQLDatabaseConfig
    ) -> str:
        """Build the formatted performance report from gathered data."""
        report_sections = []

        # Performance Report Header
        report_sections.append("# Azure SQL Database Performance Report")
        report_sections.append(f"**Database:** {db_config.database_name}")
        report_sections.append(f"**Server:** {db_config.server_name}")
        report_sections.append(f"**Generated:** {performance_data['timestamp']}")
        report_sections.append("")

        # Automatic Tuning Section
        report_sections.append("## Automatic Tuning Status")
        if "auto_tuning_error" in performance_data:
            report_sections.append(
                f"⚠️ **Error retrieving auto-tuning data:** {performance_data['auto_tuning_error']}"
            )
        else:
            auto_tuning = performance_data.get("automatic_tuning", {})
            # Handle both camelCase and snake_case field names
            desired_state = auto_tuning.get(
                "desired_state", auto_tuning.get("desiredState", "Unknown")
            )
            actual_state = auto_tuning.get(
                "actual_state", auto_tuning.get("actualState", "Unknown")
            )

            status_icon = "✅" if desired_state == actual_state else "⚠️"
            report_sections.append(f"- **Desired State**: {desired_state}")
            report_sections.append(f"- **Actual State**: {actual_state} {status_icon}")

            options = auto_tuning.get("options", {})
            for option_name, option_data in options.items():
                desired = option_data.get(
                    "desired_state", option_data.get("desiredState", "Unknown")
                )
                actual = option_data.get(
                    "actual_state", option_data.get("actualState", "Unknown")
                )
                option_icon = "✅" if desired == actual else "⚠️"
                report_sections.append(f"  - **{option_name}**: {actual} {option_icon}")
        report_sections.append("")

        # Performance Advisors Section
        report_sections.append("## Performance Advisors")
        if "advisors_error" in performance_data:
            report_sections.append(
                f"⚠️ **Error retrieving advisors:** {performance_data['advisors_error']}"
            )
        else:
            advisors = performance_data.get("advisors", [])
            if advisors:
                for advisor in advisors:
                    name = advisor.get("name", "Unknown")
                    # Handle both camelCase and snake_case field names
                    auto_execute = advisor.get(
                        "auto_execute_status",
                        advisor.get("autoExecuteStatus", "Unknown"),
                    )
                    last_checked = advisor.get(
                        "last_checked", advisor.get("lastChecked", "Never")
                    )

                    report_sections.append(f"### {name}")
                    report_sections.append(f"- **Auto Execute**: {auto_execute}")
                    report_sections.append(f"- **Last Checked**: {last_checked}")
            else:
                report_sections.append("No performance advisors available")
        report_sections.append("")

        # Recommendations Section
        report_sections.append("## Performance Recommendations")
        all_recommendations = performance_data.get("recommendations", [])
        if all_recommendations:
            active_recommendations = [
                r
                for r in all_recommendations
                if r.get("properties", {}).get("state", {}).get("currentValue")
                in ["Active", "Pending"]
            ]

            if active_recommendations:
                report_sections.append(
                    f"🚨 **{len(active_recommendations)} Active Recommendations Found**"
                )
                for rec in active_recommendations[:5]:  # Show first 5 recommendations
                    properties = rec.get("properties", {})
                    details = properties.get("details", {})

                    rec_type = details.get("indexType", "Performance")
                    impact = details.get("impactDetails", [{}])[0].get(
                        "name", "Unknown"
                    )
                    state = properties.get("state", {}).get("currentValue", "Unknown")

                    report_sections.append(
                        f"- **{rec_type} Recommendation**: {impact} impact ({state})"
                    )

                    if "indexColumns" in details:
                        columns = ", ".join(details["indexColumns"])
                        report_sections.append(f"  - **Columns**: {columns}")
            else:
                report_sections.append("✅ **No active performance recommendations**")
        else:
            report_sections.append("No performance recommendations available")

        return "\n".join(report_sections)

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            db_config = self.toolset.database_config()
            client = self.toolset.api_client()

            # Gather performance-related data
            performance_data = self._gather_performance_data(db_config, client)

            # Build the formatted report
            report_text = self._build_performance_report(performance_data, db_config)

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=report_text,
                params=params,
            )
        except Exception as e:
            error_msg = f"Failed to generate performance report: {str(e)}"
            logging.error(error_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        db_config = self.toolset.database_config()
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Analyze Database Performance ({db_config.server_name}/{db_config.database_name})"

    @staticmethod
    def validate_config(
        api_client: AzureSQLAPIClient, database_config: AzureSQLDatabaseConfig
    ) -> Tuple[bool, str]:
        errors = []

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
                errors.append(f"Database management API access denied: {error_msg}")
            else:
                errors.append(f"Database management API connection failed: {error_msg}")

        if errors:
            return False, "\n".join(errors)
        return True, ""
