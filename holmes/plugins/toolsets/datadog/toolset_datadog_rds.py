import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    StructuredToolResultStatus,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.consts import (
    TOOLSET_CONFIG_MISSING_ERROR,
    STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
)
from holmes.plugins.toolsets.datadog.datadog_api import (
    DatadogBaseConfig,
    DataDogRequestError,
    execute_datadog_http_request,
    get_headers,
)
from holmes.plugins.toolsets.utils import (
    get_param_or_raise,
    process_timestamps_to_int,
    standard_start_datetime_tool_param_description,
)

DEFAULT_TIME_SPAN_SECONDS = 3600
DEFAULT_TOP_INSTANCES = 10

# Metric definitions
LATENCY_METRICS = [
    ("aws.rds.read_latency", "Read Latency", "ms"),
    ("aws.rds.write_latency", "Write Latency", "ms"),
    ("aws.rds.commit_latency", "Commit Latency", "ms"),
    ("aws.rds.disk_queue_depth", "Disk Queue Depth", ""),
]

RESOURCE_METRICS = [
    ("aws.rds.cpuutilization", "CPU Utilization", "%"),
    ("aws.rds.database_connections", "Database Connections", "connections"),
    ("aws.rds.freeable_memory", "Freeable Memory", "bytes"),
    ("aws.rds.swap_usage", "Swap Usage", "bytes"),
]

STORAGE_METRICS = [
    ("aws.rds.read_iops", "Read IOPS", "iops"),
    ("aws.rds.write_iops", "Write IOPS", "iops"),
    ("aws.rds.burst_balance", "Burst Balance", "%"),
    ("aws.rds.free_storage_space", "Free Storage Space", "bytes"),
]


class DatadogRDSConfig(DatadogBaseConfig):
    default_time_span_seconds: int = DEFAULT_TIME_SPAN_SECONDS
    default_top_instances: int = DEFAULT_TOP_INSTANCES


class BaseDatadogRDSTool(Tool):
    toolset: "DatadogRDSToolset"


class GenerateRDSPerformanceReport(BaseDatadogRDSTool):
    def __init__(self, toolset: "DatadogRDSToolset"):
        super().__init__(
            name="datadog_rds_performance_report",
            description="[datadog/rds toolset] Generate a comprehensive performance report for a specific RDS instance including latency, resource utilization, and storage metrics with analysis",
            parameters={
                "db_instance_identifier": ToolParameter(
                    description="The RDS database instance identifier",
                    type="string",
                    required=True,
                ),
                "start_time": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end_time": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params,
            )

        try:
            db_instance = get_param_or_raise(params, "db_instance_identifier")
            start_time, end_time = process_timestamps_to_int(
                start=params.get("start_time"),
                end=params.get("end_time"),
                default_time_span_seconds=self.toolset.dd_config.default_time_span_seconds,
            )

            report: dict[str, Any] = {
                "instance_id": db_instance,
                "report_time": datetime.now(timezone.utc).isoformat(),
                "time_range": {
                    "start": datetime.fromtimestamp(
                        start_time, tz=timezone.utc
                    ).isoformat(),
                    "end": datetime.fromtimestamp(
                        end_time, tz=timezone.utc
                    ).isoformat(),
                },
                "sections": {},
                "issues": [],
                "executive_summary": "",
            }

            # Collect all metrics
            all_metrics = []
            for metric_group, group_name in [
                (LATENCY_METRICS, "latency"),
                (RESOURCE_METRICS, "resources"),
                (STORAGE_METRICS, "storage"),
            ]:
                section_data = self._collect_metrics(
                    db_instance, metric_group, start_time, end_time
                )
                if section_data:
                    report["sections"][group_name] = section_data
                    all_metrics.extend(section_data.get("metrics", {}).items())

            # Analyze metrics and generate insights
            self._analyze_metrics(report, all_metrics)

            # Generate executive summary
            report["executive_summary"] = self._generate_executive_summary(report)

            # Format the report as readable text
            formatted_report = self._format_report(report)

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=formatted_report,
                params=params,
            )

        except Exception as e:
            logging.error(f"Error generating RDS performance report: {str(e)}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to generate RDS performance report: {str(e)}",
                params=params,
            )

    def _collect_metrics(
        self,
        db_instance: str,
        metric_list: List[Tuple[str, str, str]],
        start_time: int,
        end_time: int,
    ) -> Dict[str, Any]:
        """Collect metrics for a specific group"""
        if not self.toolset.dd_config:
            raise Exception(TOOLSET_CONFIG_MISSING_ERROR)

        metrics = {}

        for metric_name, display_name, unit in metric_list:
            query = f"{metric_name}{{dbinstanceidentifier:{db_instance}}}"

            try:
                url = f"{self.toolset.dd_config.site_api_url}/api/v1/query"
                headers = get_headers(self.toolset.dd_config)
                payload = {
                    "query": query,
                    "from": start_time,
                    "to": end_time,
                }

                response = execute_datadog_http_request(
                    url=url,
                    headers=headers,
                    payload_or_params=payload,
                    timeout=self.toolset.dd_config.request_timeout,
                    method="GET",
                )

                if response and "series" in response and response["series"]:
                    series = response["series"][0]
                    points = series.get("pointlist", [])

                    if points:
                        values = [p[1] for p in points if p[1] is not None]
                        if values:
                            metrics[display_name] = {
                                "unit": unit
                                or series.get("unit", [{"short_name": ""}])[0].get(
                                    "short_name", ""
                                ),
                                "avg": round(sum(values) / len(values), 2),
                                "max": round(max(values), 2),
                                "min": round(min(values), 2),
                                "latest": round(values[-1], 2),
                                "data_points": len(values),
                            }
            except DataDogRequestError:
                continue

        return {"metrics": metrics} if metrics else {}

    def _analyze_metrics(self, report: Dict, all_metrics: List[Tuple[str, Dict]]):
        """Analyze metrics and generate issues"""
        for metric_name, data in all_metrics:
            # Latency analysis
            if "Latency" in metric_name and metric_name != "Commit Latency":
                if data["avg"] > 10:
                    report["issues"].append(
                        f"{metric_name} averaging {data['avg']}ms (above 10ms threshold)"
                    )
                if data["max"] > 50:
                    report["issues"].append(f"{metric_name} peaked at {data['max']}ms")

            # Disk queue depth
            elif metric_name == "Disk Queue Depth":
                if data["avg"] > 5:
                    report["issues"].append(
                        f"High disk queue depth (avg: {data['avg']})"
                    )

            # CPU utilization
            elif metric_name == "CPU Utilization":
                if data["avg"] > 70:
                    report["issues"].append(
                        f"High CPU utilization (avg: {data['avg']}%)"
                    )
                if data["max"] > 90:
                    report["issues"].append(
                        f"CPU saturation detected (max: {data['max']}%)"
                    )

            # Memory
            elif metric_name == "Freeable Memory":
                if data["min"] < 100 * 1024 * 1024:  # 100MB
                    report["issues"].append(
                        f"Low memory availability (min: {data['min'] / 1024 / 1024:.1f}MB)"
                    )

            # Swap usage
            elif metric_name == "Swap Usage":
                if data["avg"] > 0:
                    report["issues"].append(
                        "Swap usage detected, indicating memory pressure"
                    )

            # Burst balance
            elif metric_name == "Burst Balance":
                if data["min"] < 30:
                    report["issues"].append(
                        f"Low burst balance detected (min: {data['min']}%)"
                    )

            # IOPS
            elif "IOPS" in metric_name:
                if data["max"] > 3000:
                    report["issues"].append(
                        f"High {metric_name} (max: {data['max']} IOPS)"
                    )

    def _generate_executive_summary(self, report: Dict) -> str:
        """Generate executive summary"""
        issue_count = len(report["issues"])

        if issue_count == 0:
            return "Database is operating within normal parameters. No significant issues detected."
        elif issue_count <= 2:
            severity = "Low"
        elif issue_count <= 5:
            severity = "Medium"
        else:
            severity = "High"

        summary = f"Performance diagnosis: {severity} severity - {issue_count} issues detected.\n\n"

        # Add key findings
        if any("latency" in issue.lower() for issue in report["issues"]):
            summary += "• Latency issues affecting database response times\n"
        if any("cpu" in issue.lower() for issue in report["issues"]):
            summary += "• CPU resource constraints detected\n"
        if any(
            "memory" in issue.lower() or "swap" in issue.lower()
            for issue in report["issues"]
        ):
            summary += "• Memory pressure affecting performance\n"
        if any(
            "burst" in issue.lower() or "iops" in issue.lower()
            for issue in report["issues"]
        ):
            summary += "• Storage I/O bottlenecks identified\n"

        return summary

    def _format_report(self, report: Dict) -> str:
        """Format the report as readable text"""
        lines = []
        lines.append(f"RDS Performance Report - {report['instance_id']}")
        lines.append("=" * 70)
        lines.append(f"Generated: {report['report_time']}")
        lines.append(
            f"Time Range: {report['time_range']['start']} to {report['time_range']['end']}"
        )
        lines.append("")

        # Executive Summary
        lines.append("EXECUTIVE SUMMARY")
        lines.append("-" * 40)
        lines.append(report["executive_summary"])
        lines.append("")

        # Metrics sections
        for section_name, section_data in report["sections"].items():
            lines.append(f"{section_name.upper()} METRICS")
            lines.append("-" * 40)

            if section_data.get("metrics"):
                lines.append(
                    f"{'Metric':<25} {'Avg':>10} {'Max':>10} {'Min':>10} {'Latest':>10} {'Unit':>8}"
                )
                lines.append("-" * 80)

                for metric_name, data in section_data["metrics"].items():
                    lines.append(
                        f"{metric_name:<25} {data['avg']:>10.2f} {data['max']:>10.2f} "
                        f"{data['min']:>10.2f} {data['latest']:>10.2f} {data['unit']:>8}"
                    )
            lines.append("")

        # Issues
        if report["issues"]:
            lines.append(f"ISSUES DETECTED ({len(report['issues'])})")
            lines.append("-" * 40)
            for i, issue in enumerate(report["issues"], 1):
                lines.append(f"{i}. {issue}")
            lines.append("")

        return "\n".join(lines)

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        db_instance = params.get("db_instance_identifier", "unknown")
        return f"Generating performance report for RDS instance: {db_instance}"


class GetTopWorstPerformingRDSInstances(BaseDatadogRDSTool):
    def __init__(self, toolset: "DatadogRDSToolset"):
        super().__init__(
            name="datadog_rds_top_worst_performing",
            description="[datadog/rds toolset] Get a summarized report of the top worst performing RDS instances based on latency, CPU utilization, and error rates",
            parameters={
                "top_n": ToolParameter(
                    description=f"Number of worst performing instances to return (default: {DEFAULT_TOP_INSTANCES})",
                    type="number",
                    required=False,
                ),
                "start_time": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end_time": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "sort_by": ToolParameter(
                    description="Metric to sort by: 'latency' (default), 'cpu', 'errors', or 'composite'",
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params,
            )

        try:
            top_n = params.get("top_n", self.toolset.dd_config.default_top_instances)
            sort_by = params.get("sort_by", "latency").lower()
            start_time, end_time = process_timestamps_to_int(
                start=params.get("start_time"),
                end=params.get("end_time"),
                default_time_span_seconds=self.toolset.dd_config.default_time_span_seconds,
            )

            # Get all RDS instances
            instances = self._get_all_rds_instances(start_time, end_time)

            if not instances:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data="No RDS instances found with metrics in the specified time range",
                    params=params,
                )

            # Collect performance data for each instance
            instance_performance = []
            for instance_id in instances[:50]:  # Limit to 50 instances to avoid timeout
                perf_data = self._get_instance_performance_summary(
                    instance_id, start_time, end_time
                )
                if perf_data:
                    instance_performance.append(perf_data)

            # Sort by the specified metric
            instance_performance = self._sort_instances(instance_performance, sort_by)

            # Get top N worst performers
            worst_performers = instance_performance[:top_n]

            # Format the report
            report = self._format_summary_report(worst_performers, sort_by)

            report += f"\n\nTotal instances analyzed: {len(instance_performance)}"
            report += f"\n\nInstances:\n{json.dumps(worst_performers, indent=2)}"

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=report,
                params=params,
            )

        except Exception as e:
            logging.error(f"Error getting top worst performing RDS instances: {str(e)}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to get top worst performing RDS instances: {str(e)}",
                params=params,
            )

    def _get_all_rds_instances(self, start_time: int, end_time: int) -> List[str]:
        """Get list of all RDS instances with metrics"""
        if not self.toolset.dd_config:
            raise Exception(TOOLSET_CONFIG_MISSING_ERROR)
        try:
            # Query for any RDS metric grouped by instance
            query = "avg:aws.rds.cpuutilization{*} by {dbinstanceidentifier}"

            url = f"{self.toolset.dd_config.site_api_url}/api/v1/query"
            headers = get_headers(self.toolset.dd_config)
            payload = {
                "query": query,
                "from": start_time,
                "to": end_time,
            }

            response = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params=payload,
                timeout=self.toolset.dd_config.request_timeout,
                method="GET",
            )

            instances = []
            if response and "series" in response:
                for series in response["series"]:
                    # Extract instance ID from tags
                    scope = series.get("scope", "")
                    if "dbinstanceidentifier:" in scope:
                        instance_id = scope.split("dbinstanceidentifier:")[1].split(
                            ","
                        )[0]
                        instances.append(instance_id)

            return list(set(instances))  # Remove duplicates

        except Exception as e:
            logging.error(f"Error getting RDS instances: {str(e)}")
            return []

    def _get_instance_performance_summary(
        self, instance_id: str, start_time: int, end_time: int
    ) -> Optional[Dict]:
        """Get performance summary for a single instance"""

        if not self.toolset.dd_config:
            raise Exception(TOOLSET_CONFIG_MISSING_ERROR)

        summary: dict[str, Any] = {
            "instance_id": instance_id,
            "metrics": {},
            "score": 0,  # Composite score for sorting
            "issues": [],
        }

        # Key metrics to collect
        metrics_to_collect = [
            ("aws.rds.read_latency", "read_latency", 1.0),  # weight for composite score
            ("aws.rds.write_latency", "write_latency", 1.0),
            ("aws.rds.cpuutilization", "cpu_utilization", 0.5),
            ("aws.rds.database_connections", "connections", 0.2),
            ("aws.rds.burst_balance", "burst_balance", 0.8),
        ]

        for metric_name, key, weight in metrics_to_collect:
            query = f"avg:{metric_name}{{dbinstanceidentifier:{instance_id}}}"

            try:
                url = f"{self.toolset.dd_config.site_api_url}/api/v1/query"
                headers = get_headers(self.toolset.dd_config)
                payload = {
                    "query": query,
                    "from": start_time,
                    "to": end_time,
                }

                response = execute_datadog_http_request(
                    url=url,
                    headers=headers,
                    payload_or_params=payload,
                    timeout=self.toolset.dd_config.request_timeout,
                    method="GET",
                )

                if response and "series" in response and response["series"]:
                    series = response["series"][0]
                    points = series.get("pointlist", [])

                    if points:
                        values = [p[1] for p in points if p[1] is not None]
                        if values:
                            avg_value = sum(values) / len(values)
                            max_value = max(values)

                            summary["metrics"][key] = {
                                "avg": round(avg_value, 2),
                                "max": round(max_value, 2),
                            }

                            # Calculate contribution to composite score
                            if key in ["read_latency", "write_latency"]:
                                # Higher latency = worse performance
                                score_contrib = avg_value * weight
                                if avg_value > 10:
                                    summary["issues"].append(
                                        f"High {key.replace('_', ' ')}: {avg_value:.1f}ms"
                                    )
                            elif key == "cpu_utilization":
                                # Higher CPU = worse performance
                                score_contrib = avg_value * weight
                                if avg_value > 70:
                                    summary["issues"].append(
                                        f"High CPU: {avg_value:.1f}%"
                                    )
                            elif key == "burst_balance":
                                # Lower burst balance = worse performance
                                score_contrib = (100 - avg_value) * weight
                                if avg_value < 30:
                                    summary["issues"].append(
                                        f"Low burst balance: {avg_value:.1f}%"
                                    )
                            else:
                                score_contrib = 0

                            summary["score"] += score_contrib

            except Exception:
                continue

        return summary if summary["metrics"] else None

    def _sort_instances(self, instances: List[Dict], sort_by: str) -> List[Dict]:
        """Sort instances by specified metric"""
        if sort_by == "latency":
            # Sort by average of read and write latency
            def latency_key(inst):
                read_lat = inst["metrics"].get("read_latency", {}).get("avg", 0)
                write_lat = inst["metrics"].get("write_latency", {}).get("avg", 0)
                return (read_lat + write_lat) / 2

            return sorted(instances, key=latency_key, reverse=True)

        elif sort_by == "cpu":
            return sorted(
                instances,
                key=lambda x: x["metrics"].get("cpu_utilization", {}).get("avg", 0),
                reverse=True,
            )

        elif sort_by == "composite":
            return sorted(instances, key=lambda x: x["score"], reverse=True)

        else:  # Default to latency
            return self._sort_instances(instances, "latency")

    def _format_summary_report(self, instances: List[Dict], sort_by: str) -> str:
        """Format the summary report"""
        lines = []
        lines.append("Top Worst Performing RDS Instances")
        lines.append("=" * 70)
        lines.append(f"Sorted by: {sort_by}")
        lines.append(f"Instances shown: {len(instances)}")
        lines.append("")

        for rank, inst in enumerate(instances, 1):
            lines.append(f"{rank}. {inst['instance_id']}")
            lines.append("-" * 40)

            # Show key metrics
            metrics = inst["metrics"]
            if "read_latency" in metrics:
                lines.append(
                    f"   Read Latency:  {metrics['read_latency']['avg']:.1f}ms avg, {metrics['read_latency']['max']:.1f}ms max"
                )
            if "write_latency" in metrics:
                lines.append(
                    f"   Write Latency: {metrics['write_latency']['avg']:.1f}ms avg, {metrics['write_latency']['max']:.1f}ms max"
                )
            if "cpu_utilization" in metrics:
                lines.append(
                    f"   CPU Usage:     {metrics['cpu_utilization']['avg']:.1f}% avg, {metrics['cpu_utilization']['max']:.1f}% max"
                )
            if "burst_balance" in metrics:
                lines.append(
                    f"   Burst Balance: {metrics['burst_balance']['avg']:.1f}% avg"
                )

            # Show issues
            if inst["issues"]:
                lines.append("   Issues:")
                for issue in inst["issues"]:
                    lines.append(f"   • {issue}")

            lines.append("")

        return "\n".join(lines)

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        top_n = params.get("top_n", DEFAULT_TOP_INSTANCES)
        sort_by = params.get("sort_by", "latency")
        return f"Getting top {top_n} worst performing RDS instances sorted by {sort_by}"


class DatadogRDSToolset(Toolset):
    dd_config: Optional[DatadogRDSConfig] = None

    def __init__(self):
        super().__init__(
            name="datadog/rds",
            description="Analyze RDS database performance and identify worst performers using Datadog metrics",
            tags=[ToolsetTag.CORE],
            tools=[
                GenerateRDSPerformanceReport(toolset=self),
                GetTopWorstPerformingRDSInstances(toolset=self),
            ],
        )

    def prerequisites_check(self, config: Dict[str, Any]) -> CallablePrerequisite:
        def check_datadog_connectivity(config_dict: Dict[str, Any]) -> Tuple[bool, str]:
            """Check Datadog API connectivity and permissions"""
            try:
                # Validate config
                self.dd_config = DatadogRDSConfig(**config_dict)

                # Test API connectivity
                url = f"{self.dd_config.site_api_url}/api/v1/validate"
                headers = get_headers(self.dd_config)

                response = execute_datadog_http_request(
                    url=url,
                    headers=headers,
                    payload_or_params={},
                    timeout=self.dd_config.request_timeout,
                    method="GET",
                )

                if response and response.get("valid", False):
                    # Test metrics API access
                    metrics_url = f"{self.dd_config.site_api_url}/api/v1/metrics"
                    execute_datadog_http_request(
                        url=metrics_url,
                        headers=headers,
                        payload_or_params={"from": 0},
                        timeout=self.dd_config.request_timeout,
                        method="GET",
                    )
                    return True, ""
                else:
                    return False, "Invalid Datadog API credentials"

            except DataDogRequestError as e:
                if e.status_code == 403:
                    return False, "Invalid Datadog API keys or insufficient permissions"
                else:
                    return False, f"Datadog API error: {str(e)}"
            except Exception as e:
                return False, f"Failed to initialize Datadog RDS toolset: {str(e)}"

        return CallablePrerequisite(callable=check_datadog_connectivity)

    def post_init(self, config: dict):
        """Load LLM instructions after initialization"""
        self._reload_instructions()

    def _reload_instructions(self):
        """Load RDS analysis specific instructions"""
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "datadog_rds_instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

    def get_example_config(self) -> Dict[str, Any]:
        """Get example configuration for this toolset."""
        return {
            "dd_api_key": "your-datadog-api-key",
            "dd_app_key": "your-datadog-application-key",
            "site_api_url": "https://api.datadoghq.com",
            "default_time_span_seconds": 3600,
            "default_top_instances": 10,
        }
