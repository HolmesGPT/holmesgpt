from typing import Dict, List, Any
import logging
from datetime import datetime, timezone, timedelta
from azure.core.credentials import TokenCredential
from azure.mgmt.monitor import MonitorManagementClient


class ConnectionFailureAPI:
    """API client for analyzing Azure SQL Database connection failures and patterns."""

    def __init__(
        self,
        credential: TokenCredential,
        subscription_id: str,
    ):
        self.credential = credential
        self.subscription_id = subscription_id
        self.monitor_client = MonitorManagementClient(credential, subscription_id)

    def _build_database_resource_id(
        self, resource_group: str, server_name: str, database_name: str
    ) -> str:
        """Build the full Azure resource ID for the SQL database."""
        return (
            f"/subscriptions/{self.subscription_id}/"
            f"resourceGroups/{resource_group}/"
            f"providers/Microsoft.Sql/servers/{server_name}/"
            f"databases/{database_name}"
        )

    def _build_server_resource_id(self, resource_group: str, server_name: str) -> str:
        """Build the full Azure resource ID for the SQL server."""
        return (
            f"/subscriptions/{self.subscription_id}/"
            f"resourceGroups/{resource_group}/"
            f"providers/Microsoft.Sql/servers/{server_name}"
        )

    def analyze_connection_failures(
        self,
        resource_group: str,
        server_name: str,
        database_name: str,
        hours_back: int = 24,
    ) -> Dict[str, Any]:
        """Analyze connection failures and patterns for the SQL database."""
        try:
            database_resource_id = self._build_database_resource_id(
                resource_group, server_name, database_name
            )
            server_resource_id = self._build_server_resource_id(
                resource_group, server_name
            )

            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=hours_back)

            # Connection-related metrics to analyze (database-level only)
            connection_metrics = [
                "connection_failed",
                "connection_successful",
                "blocked_by_firewall",
                "connection_failed_user_error",
                "sessions_count",
                "sessions_percent",
                "workers_percent",
            ]

            # Get connection metrics (only from database, not server)
            connection_data = self._get_connection_metrics(
                database_resource_id, connection_metrics, start_time, end_time
            )

            # Server-level metrics are not available for connection failures
            # Only DTU and storage metrics are available at server level
            server_connection_data = {
                "note": "Connection metrics only available at database level"
            }

            # Analyze activity logs for connection-related events
            activity_log_data = self._analyze_connection_activity_logs(
                database_resource_id, server_resource_id, start_time, end_time
            )

            # Combine and analyze all data
            analysis = self._analyze_connection_patterns(
                connection_data, server_connection_data, activity_log_data
            )

            return {
                "database_resource_id": database_resource_id,
                "server_resource_id": server_resource_id,
                "time_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                    "hours": hours_back,
                },
                "connection_metrics": connection_data,
                "server_metrics": server_connection_data,
                "activity_events": activity_log_data,
                "analysis": analysis,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            error_msg = f"Failed to analyze connection failures: {str(e)}"
            logging.error(error_msg, exc_info=True)
            return {"error": error_msg}

    def _get_connection_metrics(
        self,
        resource_id: str,
        metric_names: List[str],
        start_time: datetime,
        end_time: datetime,
    ) -> Dict[str, Any]:
        """Get connection-related metrics from Azure Monitor."""
        try:
            metrics_data = {}

            for metric_name in metric_names:
                try:
                    # Get metric data with proper ISO 8601 format
                    timespan = f"{start_time.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}/{end_time.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}"
                    metrics = self.monitor_client.metrics.list(
                        resource_uri=resource_id,
                        timespan=timespan,
                        interval="PT1H",  # 1-hour intervals
                        metricnames=metric_name,
                        aggregation="Total,Average,Maximum",
                    )

                    metric_values = []
                    for metric in metrics.value:
                        if metric.timeseries:
                            for timeseries in metric.timeseries:
                                if timeseries.data is None:
                                    continue
                                for data_point in timeseries.data:
                                    if data_point.time_stamp:
                                        metric_values.append(
                                            {
                                                "timestamp": data_point.time_stamp.isoformat(),
                                                "total": data_point.total,
                                                "average": data_point.average,
                                                "maximum": data_point.maximum,
                                            }
                                        )

                    metrics_data[metric_name] = {
                        "values": metric_values,
                        "total_data_points": len(metric_values),
                    }

                except Exception as e:
                    # Only log as warning if it's not a known metric availability issue
                    error_msg = str(e)
                    if "Failed to find metric configuration" in error_msg:
                        logging.info(
                            f"Metric {metric_name} not available for this resource type"
                        )
                    else:
                        logging.warning(f"Failed to get metric {metric_name}: {e}")

                    metrics_data[metric_name] = {
                        "error": str(e),
                        "values": [],
                        "total_data_points": 0,
                    }

            return metrics_data

        except Exception as e:
            logging.error(f"Failed to get connection metrics: {e}")
            return {"error": str(e)}

    def _get_server_connection_metrics(
        self, server_resource_id: str, start_time: datetime, end_time: datetime
    ) -> Dict[str, Any]:
        """Get server-level connection metrics - Note: Connection metrics not available at server level."""
        # Connection failure metrics are only available at database level
        # Server level only has DTU and storage metrics
        return {
            "note": "Connection failure metrics are only available at database level",
            "available_server_metrics": [
                "dtu_consumption_percent",
                "storage_used",
                "dtu_used",
            ],
            "connection_metrics_location": "database_level_only",
        }

    def _analyze_connection_activity_logs(
        self,
        database_resource_id: str,
        server_resource_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Dict[str, Any]:
        """Analyze activity logs for connection-related events."""
        try:
            # Connection-related operation names to look for
            connection_operations = [
                "Microsoft.Sql/servers/databases/connect",
                "Microsoft.Sql/servers/connect",
                "Microsoft.Sql/servers/databases/disconnect",
                "Microsoft.Sql/servers/firewallRules/write",
                "Microsoft.Sql/servers/connectionPolicies/write",
            ]

            # Activity logs filter - remove unsupported level filter
            filter_query = (
                f"eventTimestamp ge '{start_time.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}' and "
                f"eventTimestamp le '{end_time.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}' and "
                f"resourceId eq '{database_resource_id}'"
            )

            activity_logs = self.monitor_client.activity_logs.list(filter=filter_query)

            connection_events = []
            for log_entry in activity_logs:
                if hasattr(log_entry, "operation_name") and log_entry.operation_name:
                    operation_name = (
                        log_entry.operation_name.value
                        if hasattr(log_entry.operation_name, "value")
                        else str(log_entry.operation_name)
                    )

                    # Check if this is a connection-related operation
                    is_connection_related = any(
                        op in operation_name for op in connection_operations
                    ) or any(
                        keyword in operation_name.lower()
                        for keyword in [
                            "connect",
                            "firewall",
                            "auth",
                            "login",
                            "security",
                        ]
                    )

                    # Filter by level after getting the data, since level filter isn't supported in query
                    if is_connection_related or (
                        hasattr(log_entry, "level")
                        and log_entry.level in ["Warning", "Error", "Critical"]
                    ):
                        event_data = {
                            "timestamp": getattr(
                                log_entry, "event_timestamp", end_time
                            ).isoformat(),
                            "operation_name": operation_name,
                            "level": getattr(log_entry, "level", "Unknown"),
                            "status": getattr(log_entry, "status", {}).get(
                                "value", "Unknown"
                            )
                            if hasattr(getattr(log_entry, "status", {}), "get")
                            else "Unknown",
                            "caller": getattr(log_entry, "caller", "Unknown"),
                            "description": getattr(
                                log_entry, "description", "No description"
                            ),
                            "resource_id": getattr(log_entry, "resource_id", ""),
                            "correlation_id": getattr(log_entry, "correlation_id", ""),
                            "is_connection_related": is_connection_related,
                        }
                        connection_events.append(event_data)

            # Sort by timestamp, most recent first
            connection_events.sort(key=lambda x: x["timestamp"], reverse=True)

            return {
                "events": connection_events,
                "total_events": len(connection_events),
                "connection_related_events": len(
                    [e for e in connection_events if e["is_connection_related"]]
                ),
                "error_events": len(
                    [
                        e
                        for e in connection_events
                        if e["level"] in ["Error", "Critical"]
                    ]
                ),
                "warning_events": len(
                    [e for e in connection_events if e["level"] == "Warning"]
                ),
            }

        except Exception as e:
            logging.error(f"Failed to analyze connection activity logs: {e}")
            return {"error": str(e), "events": [], "total_events": 0}

    def _analyze_connection_patterns(
        self,
        connection_data: Dict[str, Any],
        server_data: Dict[str, Any],
        activity_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Analyze connection patterns and identify issues."""
        analysis: dict = {
            "summary": {},
            "issues_detected": [],
            "recommendations": [],
            "metrics_analysis": {},
        }

        try:
            # Analyze connection failure metrics
            if "connection_failed" in connection_data and connection_data[
                "connection_failed"
            ].get("values"):
                failed_connections = connection_data["connection_failed"]["values"]
                total_failures = sum(
                    dp.get("total", 0) or 0 for dp in failed_connections
                )
                max_failures_per_hour = max(
                    (dp.get("maximum", 0) or 0 for dp in failed_connections), default=0
                )

                analysis["metrics_analysis"]["connection_failures"] = {
                    "total_failed_connections": total_failures,
                    "max_failures_per_hour": max_failures_per_hour,
                    "failure_trend": "increasing"
                    if len(failed_connections) > 1
                    and (failed_connections[-1].get("total", 0) or 0)
                    > (failed_connections[0].get("total", 0) or 0)
                    else "stable",
                }

                if total_failures > 0:
                    analysis["issues_detected"].append(
                        f"🔴 {int(total_failures)} connection failures detected"
                    )
                    if max_failures_per_hour > 10:
                        analysis["issues_detected"].append(
                            f"⚠️ High failure rate: {int(max_failures_per_hour)} failures in single hour"
                        )

            # Analyze firewall blocks
            if "blocked_by_firewall" in connection_data and connection_data[
                "blocked_by_firewall"
            ].get("values"):
                firewall_blocks = connection_data["blocked_by_firewall"]["values"]
                total_blocks = sum(dp.get("total", 0) or 0 for dp in firewall_blocks)

                if total_blocks > 0:
                    analysis["issues_detected"].append(
                        f"🚫 {int(total_blocks)} connections blocked by firewall"
                    )
                    analysis["recommendations"].append(
                        "Review firewall rules - clients may be connecting from unauthorized IP addresses"
                    )

            # Analyze successful connections for context
            if "connection_successful" in connection_data and connection_data[
                "connection_successful"
            ].get("values"):
                successful_connections = connection_data["connection_successful"][
                    "values"
                ]
                total_successful = sum(
                    dp.get("total", 0) or 0 for dp in successful_connections
                )

                analysis["metrics_analysis"]["successful_connections"] = {
                    "total_successful_connections": total_successful
                }

                # Calculate failure rate if we have both metrics
                if "connection_failures" in analysis["metrics_analysis"]:
                    total_failures = analysis["metrics_analysis"][
                        "connection_failures"
                    ]["total_failed_connections"]
                    if total_successful + total_failures > 0:
                        failure_rate = (
                            total_failures / (total_successful + total_failures)
                        ) * 100
                        analysis["metrics_analysis"]["failure_rate_percent"] = round(
                            failure_rate, 2
                        )

                        if failure_rate > 5:
                            analysis["issues_detected"].append(
                                f"📊 High connection failure rate: {failure_rate:.1f}%"
                            )

            # Analyze activity log events
            if "events" in activity_data and activity_data["events"]:
                error_events = [
                    e
                    for e in activity_data["events"]
                    if e["level"] in ["Error", "Critical"]
                ]
                if error_events:
                    analysis["issues_detected"].append(
                        f"📋 {len(error_events)} error-level events in activity logs"
                    )

                # Look for specific patterns
                auth_events = [
                    e
                    for e in activity_data["events"]
                    if "auth" in e["operation_name"].lower()
                    or "login" in e["operation_name"].lower()
                ]
                if auth_events:
                    analysis["issues_detected"].append(
                        f"🔐 {len(auth_events)} authentication-related events detected"
                    )

            # Generate recommendations based on findings
            if not analysis["issues_detected"]:
                analysis["summary"]["status"] = "healthy"
                analysis["summary"]["message"] = (
                    "✅ No significant connection issues detected"
                )
            else:
                analysis["summary"]["status"] = "issues_detected"
                analysis["summary"]["message"] = (
                    f"⚠️ {len(analysis['issues_detected'])} connection issues detected"
                )

                # Add general recommendations
                if any(
                    "failure" in issue.lower() for issue in analysis["issues_detected"]
                ):
                    analysis["recommendations"].extend(
                        [
                            "Monitor application connection pooling configuration",
                            "Check for network connectivity issues between client and server",
                            "Review connection timeout settings in application",
                        ]
                    )

                if any(
                    "firewall" in issue.lower() for issue in analysis["issues_detected"]
                ):
                    analysis["recommendations"].append(
                        "Validate client IP addresses against firewall rules"
                    )

        except Exception as e:
            logging.error(f"Failed to analyze connection patterns: {e}")
            analysis["error"] = str(e)

        return analysis
