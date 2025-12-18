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
from holmes.plugins.toolsets.azure_sql.apis.storage_analysis_api import (
    StorageAnalysisAPI,
)
from holmes.plugins.toolsets.azure_sql.apis.azure_sql_api import AzureSQLAPIClient
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class AnalyzeDatabaseStorage(BaseAzureSQLTool):
    def __init__(self, toolset: "BaseAzureSQLToolset"):
        super().__init__(
            name="analyze_database_storage",
            description="Analyzes database storage utilization including disk usage, growth trends, file-level details, and table space consumption. Use this for capacity planning and storage optimization.",
            parameters={
                "hours_back": ToolParameter(
                    description="Time window for storage metrics analysis in hours. Use 24 for daily trends, 168 for weekly analysis (default: 24)",
                    type="integer",
                    required=False,
                ),
                "top_tables": ToolParameter(
                    description="Number of largest tables to analyze for space usage. Use 20 for comprehensive view, 10 for quick overview (default: 20)",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _build_storage_report(
        self,
        db_config: AzureSQLDatabaseConfig,
        storage_data: Dict,
        hours_back: int,
        top_tables: int,
    ) -> str:
        """Build the formatted storage report from gathered data."""
        report_sections = []

        # Header
        report_sections.append("# Azure SQL Database Storage Analysis Report")
        report_sections.append(f"**Database:** {db_config.database_name}")
        report_sections.append(f"**Server:** {db_config.server_name}")
        report_sections.append(f"**Analysis Period:** Last {hours_back} hours")
        report_sections.append(
            f"**Generated:** {datetime.now(timezone.utc).isoformat()}"
        )
        report_sections.append("")

        # Storage Summary
        report_sections.append("## Storage Summary")
        summary = storage_data.get("summary", {})
        if "error" in summary:
            report_sections.append(
                f"⚠️ **Error retrieving storage summary:** {summary['error']}"
            )
        else:
            total_size = summary.get("total_database_size_mb", 0) or 0
            used_size = summary.get("total_used_size_mb", 0) or 0
            data_size = summary.get("total_data_size_mb", 0) or 0
            log_size = summary.get("total_log_size_mb", 0) or 0

            if total_size:
                used_percent = (used_size / total_size) * 100
                free_size = total_size - used_size

                report_sections.append(
                    f"- **Total Database Size**: {total_size:,.1f} MB"
                )
                report_sections.append(
                    f"- **Used Space**: {used_size:,.1f} MB ({used_percent:.1f}%)"
                )
                report_sections.append(f"- **Free Space**: {free_size:,.1f} MB")
                report_sections.append(f"- **Data Files**: {data_size:,.1f} MB")
                report_sections.append(f"- **Log Files**: {log_size:,.1f} MB")
                report_sections.append(
                    f"- **Data Files Count**: {summary.get('data_files_count', 0)}"
                )
                report_sections.append(
                    f"- **Log Files Count**: {summary.get('log_files_count', 0)}"
                )
            else:
                report_sections.append("No storage summary data available")
        report_sections.append("")

        # File Details
        report_sections.append("## Database Files Details")
        file_details = storage_data.get("file_details", [])
        if isinstance(file_details, dict) and "error" in file_details:
            report_sections.append(
                f"⚠️ **Error retrieving file details:** {file_details['error']}"
            )
        elif file_details:
            for file_info in file_details:
                file_type = file_info.get("file_type", "Unknown")
                logical_name = file_info.get("logical_name", "Unknown")
                size_mb = file_info.get("size_mb", 0) or 0
                used_mb = file_info.get("used_mb")
                used_percent = file_info.get("used_percent")
                max_size = file_info.get("max_size", "Unknown")
                growth = file_info.get("growth_setting", "Unknown")

                # Only calculate status icon if we have used_percent data
                if used_percent is not None:
                    status_icon = (
                        "🔴"
                        if used_percent > 90
                        else "🟡"
                        if used_percent > 75
                        else "🟢"
                    )
                else:
                    status_icon = ""

                report_sections.append(f"### {file_type} File: {logical_name}")
                report_sections.append(f"- **Size**: {size_mb:,.1f} MB")
                if used_mb is not None and used_percent is not None:
                    report_sections.append(
                        f"- **Used**: {used_mb:,.1f} MB ({used_percent:.1f}%) {status_icon}"
                    )
                else:
                    report_sections.append("- **Used**: N/A (FILESTREAM file)")
                report_sections.append(f"- **Max Size**: {max_size}")
                report_sections.append(f"- **Growth**: {growth}")
                report_sections.append("")
        else:
            report_sections.append("No file details available")

        # Growth Trend Analysis
        report_sections.append("## Storage Growth Analysis")
        growth_data = storage_data.get("growth_trend", {})
        if "error" in growth_data:
            report_sections.append(
                f"⚠️ **Growth analysis unavailable:** {growth_data['error']}"
            )
        elif growth_data.get("growth_analysis"):
            analysis = growth_data["growth_analysis"]
            total_growth = analysis.get("total_growth_mb", 0) or 0
            growth_percent = analysis.get("growth_percent", 0) or 0
            days_analyzed = analysis.get("days_analyzed", 0) or 0
            daily_growth = analysis.get("avg_daily_growth_mb", 0) or 0

            growth_icon = (
                "🔴" if daily_growth > 100 else "🟡" if daily_growth > 50 else "🟢"
            )

            report_sections.append(f"- **Analysis Period**: {days_analyzed} days")
            report_sections.append(
                f"- **Total Growth**: {total_growth:,.1f} MB ({growth_percent:.1f}%)"
            )
            report_sections.append(
                f"- **Daily Average Growth**: {daily_growth:,.1f} MB {growth_icon}"
            )

            # Growth projection
            if daily_growth > 0:
                days_to_double = (
                    (summary.get("total_database_size_mb", 0) / daily_growth)
                    if daily_growth > 0
                    else 0
                )
                report_sections.append(
                    f"- **Projected to Double**: {days_to_double:,.0f} days"
                )
        else:
            report_sections.append(
                "Growth analysis requires backup history (not available)"
            )
        report_sections.append("")

        # Top Tables by Space Usage
        report_sections.append(f"## Top {top_tables} Tables by Space Usage")
        table_usage = storage_data.get("table_usage", [])
        if table_usage:
            report_sections.append("")
            for i, table in enumerate(table_usage[:top_tables], 1):
                schema_name = table.get("schema_name", "unknown")
                table_name = table.get("table_name", "unknown")
                total_space = table.get("total_space_mb", 0)
                row_count = table.get("row_count", 0)
                index_type = table.get("index_type", "unknown")

                report_sections.append(f"### {i}. {schema_name}.{table_name}")
                report_sections.append(f"- **Total Space**: {total_space:,.1f} MB")
                report_sections.append(f"- **Row Count**: {row_count:,}")
                report_sections.append(f"- **Index Type**: {index_type}")
                report_sections.append("")
        else:
            report_sections.append("No table usage data available")

        # Azure Monitor Storage Metrics
        report_sections.append("## Azure Monitor Storage Metrics")
        metrics = storage_data.get("metrics", {})
        if "error" in metrics:
            report_sections.append(f"⚠️ **Metrics unavailable:** {metrics['error']}")
        else:
            metric_found = False
            for metric_name, metric_data in metrics.items():
                if metric_data:
                    metric_found = True
                    recent_values = metric_data[-5:]  # Last 5 data points
                    if recent_values:
                        avg_value = sum(
                            point.get("average", 0) or 0 for point in recent_values
                        ) / len(recent_values)
                        max_value = max(
                            point.get("maximum", 0) or 0 for point in recent_values
                        )

                        # Format based on metric type
                        if "percent" in metric_name:
                            report_sections.append(
                                f"- **{metric_name}**: Avg {avg_value:.1f}%, Max {max_value:.1f}%"
                            )
                        else:
                            report_sections.append(
                                f"- **{metric_name}**: Avg {avg_value:,.1f}, Max {max_value:,.1f}"
                            )

            if not metric_found:
                report_sections.append("No recent storage metric data available")

        # TempDB Usage
        tempdb_data = storage_data.get("tempdb", {})
        if tempdb_data and "error" not in tempdb_data:
            report_sections.append("")
            report_sections.append("## TempDB Usage")
            for metric_type, data in tempdb_data.items():
                if isinstance(data, dict):
                    used_percent = data.get("used_percent", 0) or 0
                    status_icon = (
                        "🔴"
                        if used_percent > 90
                        else "🟡"
                        if used_percent > 75
                        else "🟢"
                    )
                    report_sections.append(
                        f"- **{metric_type}**: {data.get('used_size_mb', 0) or 0:,.1f} MB / {data.get('total_size_mb', 0) or 0:,.1f} MB ({used_percent:.1f}%) {status_icon}"
                    )

        return "\n".join(report_sections)

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            hours_back = params.get("hours_back", 24)
            top_tables = params.get("top_tables", 20)

            db_config = self.toolset.database_config()

            # Create storage analysis API client
            api_client = self.toolset.api_client()
            storage_api = StorageAnalysisAPI(
                credential=api_client.credential,
                subscription_id=db_config.subscription_id,
            )

            # Gather storage data
            storage_data: Dict[str, Any] = {}

            # Get storage summary
            storage_data["summary"] = storage_api.get_storage_summary(
                db_config.server_name, db_config.database_name
            )

            # Get file details
            storage_data["file_details"] = storage_api.get_database_size_details(
                db_config.server_name, db_config.database_name
            )

            # Get table space usage
            storage_data["table_usage"] = storage_api.get_table_space_usage(
                db_config.server_name, db_config.database_name, top_tables
            )

            # Get growth trend
            storage_data["growth_trend"] = storage_api.get_storage_growth_trend(
                db_config.server_name, db_config.database_name
            )

            # Get Azure Monitor storage metrics
            storage_data["metrics"] = storage_api.get_storage_metrics(
                db_config.resource_group,
                db_config.server_name,
                db_config.database_name,
                hours_back,
            )

            # Get TempDB usage
            storage_data["tempdb"] = storage_api.get_tempdb_usage(
                db_config.server_name, db_config.database_name
            )

            # Build the formatted report
            report_text = self._build_storage_report(
                db_config, storage_data, hours_back, top_tables
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=report_text,
                params=params,
            )
        except Exception as e:
            error_msg = f"Failed to generate storage report: {str(e)}"
            logging.error(error_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        db_config = self.toolset.database_config()
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Analyze Database Storage ({db_config.server_name}/{db_config.database_name})"

    @staticmethod
    def validate_config(
        api_client: AzureSQLAPIClient, database_config: AzureSQLDatabaseConfig
    ) -> Tuple[bool, str]:
        errors = []

        # Create storage analysis API client for validation
        storage_api = StorageAnalysisAPI(
            credential=api_client.credential,
            subscription_id=database_config.subscription_id,
        )

        # Test SQL database connection (storage queries)
        try:
            storage_api.get_storage_summary(
                database_config.server_name, database_config.database_name
            )
        except Exception as e:
            error_msg = str(e)
            if "authentication" in error_msg.lower() or "login" in error_msg.lower():
                errors.append(f"SQL database authentication failed: {error_msg}")
            elif (
                "permission" in error_msg.lower()
                or "authorization" in error_msg.lower()
            ):
                errors.append(f"SQL database permissions insufficient: {error_msg}")
            else:
                errors.append(f"SQL database connection failed: {error_msg}")

        # Test Azure Monitor API access (storage metrics)
        try:
            storage_api.get_storage_metrics(
                database_config.resource_group,
                database_config.server_name,
                database_config.database_name,
                1,  # Test with 1 hour
            )
        except Exception as e:
            error_msg = str(e)
            if (
                "authorization" in error_msg.lower()
                or "permission" in error_msg.lower()
            ):
                errors.append(f"Azure Monitor API access denied: {error_msg}")
            else:
                errors.append(f"Azure Monitor API connection failed: {error_msg}")

        if errors:
            return False, "\n".join(errors)
        return True, ""
