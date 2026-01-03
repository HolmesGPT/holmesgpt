import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from azure.core.credentials import TokenCredential
from azure.mgmt.monitor import MonitorManagementClient
from azure.mgmt.resource import ResourceManagementClient


class AlertMonitoringAPI:
    """API client for Azure Monitor alerts related to Azure SQL databases."""

    def __init__(
        self,
        credential: TokenCredential,
        subscription_id: str,
    ):
        self.credential = credential
        self.subscription_id = subscription_id
        self.monitor_client = MonitorManagementClient(credential, subscription_id)
        self.resource_client = ResourceManagementClient(credential, subscription_id)

        # Initialize alerts management client (different from monitor client)
        try:
            # Import here to avoid circular imports
            from azure.mgmt.alertsmanagement import AlertsManagementClient

            self.alerts_client = AlertsManagementClient(credential, subscription_id)
        except ImportError:
            logging.warning(
                "AlertsManagementClient not available, using fallback methods"
            )
            self.alerts_client = None

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

    def get_active_alerts(
        self, resource_group: str, server_name: str, database_name: str
    ) -> Dict[str, Any]:
        """Get currently active alerts for the SQL database and server."""
        try:
            database_resource_id = self._build_database_resource_id(
                resource_group, server_name, database_name
            )
            server_resource_id = self._build_server_resource_id(
                resource_group, server_name
            )

            active_alerts = []

            if self.alerts_client:
                # Get alerts using the AlertsManagement API
                try:
                    # Get database-specific alerts
                    db_alerts = self.alerts_client.alerts.get_all(
                        target_resource=database_resource_id,
                        alert_state="New,Acknowledged",
                    )
                    for alert in db_alerts:
                        active_alerts.append(self._format_alert(alert, "database"))

                    # Get server-level alerts that might affect the database
                    server_alerts = self.alerts_client.alerts.get_all(
                        target_resource=server_resource_id,
                        alert_state="New,Acknowledged",
                    )
                    for alert in server_alerts:
                        active_alerts.append(self._format_alert(alert, "server"))

                except Exception as e:
                    logging.warning(f"AlertsManagement API failed, using fallback: {e}")
                    return self._get_active_alerts_fallback(
                        resource_group, server_name, database_name
                    )
            else:
                # Fallback method using Monitor API
                return self._get_active_alerts_fallback(
                    resource_group, server_name, database_name
                )

            return {
                "database_resource_id": database_resource_id,
                "server_resource_id": server_resource_id,
                "active_alerts": active_alerts,
                "total_count": len(active_alerts),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            error_msg = f"Failed to retrieve active alerts: {str(e)}"
            logging.error(error_msg, exc_info=True)
            return {"error": error_msg}

    def _get_active_alerts_fallback(
        self, resource_group: str, server_name: str, database_name: str
    ) -> Dict[str, Any]:
        """Fallback method to get alerts using Monitor API activity log."""
        try:
            database_resource_id = self._build_database_resource_id(
                resource_group, server_name, database_name
            )
            server_resource_id = self._build_server_resource_id(
                resource_group, server_name
            )

            # Get recent activity log entries that might indicate alerts
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=24)

            filter_query = (
                f"eventTimestamp ge '{start_time.isoformat()}' and "
                f"eventTimestamp le '{end_time.isoformat()}' and "
                f"(resourceId eq '{database_resource_id}' or resourceId eq '{server_resource_id}') and "
                f"(level eq 'Warning' or level eq 'Error')"
            )

            activity_logs = self.monitor_client.activity_logs.list(filter=filter_query)

            alerts = []
            for log_entry in activity_logs:
                if hasattr(log_entry, "operation_name") and log_entry.operation_name:
                    # Convert activity log to alert-like format
                    alert_data = {
                        "id": getattr(log_entry, "event_data_id", "unknown"),
                        "name": getattr(log_entry, "operation_name", {}).get(
                            "value", "Unknown Operation"
                        ),
                        "description": getattr(
                            log_entry, "description", "No description available"
                        ),
                        "severity": self._map_level_to_severity(
                            getattr(log_entry, "level", "Informational")
                        ),
                        "state": "Active",
                        "monitor_condition": "Fired",
                        "fired_time": getattr(
                            log_entry, "event_timestamp", end_time
                        ).isoformat(),
                        "resource_type": "Activity Log Event",
                        "target_resource": getattr(log_entry, "resource_id", ""),
                        "scope": "server"
                        if server_resource_id
                        in str(getattr(log_entry, "resource_id", ""))
                        else "database",
                    }
                    alerts.append(alert_data)

            return {
                "database_resource_id": database_resource_id,
                "server_resource_id": server_resource_id,
                "active_alerts": alerts,
                "total_count": len(alerts),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "method": "activity_log_fallback",
            }

        except Exception as e:
            error_msg = f"Failed to retrieve alerts using fallback method: {str(e)}"
            logging.error(error_msg, exc_info=True)
            return {"error": error_msg}

    def get_alert_history(
        self,
        resource_group: str,
        server_name: str,
        database_name: str,
        hours_back: int = 168,  # Default to 7 days
    ) -> Dict[str, Any]:
        """Get historical alerts for the SQL database and server by fetching metric alert rules."""
        try:
            database_resource_id = self._build_database_resource_id(
                resource_group, server_name, database_name
            )
            server_resource_id = self._build_server_resource_id(
                resource_group, server_name
            )

            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=hours_back)

            historical_alerts = []

            try:
                # Get metric alert rules from the resource group
                logging.info(
                    f"Fetching metric alert rules from resource group: {resource_group}"
                )
                metric_alert_rules = (
                    self.monitor_client.metric_alerts.list_by_resource_group(
                        resource_group
                    )
                )

                relevant_rules = []
                for rule in metric_alert_rules:
                    # Check if this rule applies to our database or server
                    if hasattr(rule, "scopes") and rule.scopes:
                        for scope in rule.scopes:
                            if (
                                database_resource_id in scope
                                or server_resource_id in scope
                            ):
                                relevant_rules.append(rule)
                                break

                logging.info(
                    f"Found {len(relevant_rules)} metric alert rules relevant to our resources"
                )

                # For each relevant rule, try to get alert instances if possible
                for rule in relevant_rules:
                    rule_name = getattr(rule, "name", "Unknown Rule")
                    rule_id = getattr(rule, "id", "Unknown ID")

                    # Create alert entry from rule definition
                    scope_type = (
                        "database"
                        if database_resource_id in str(getattr(rule, "scopes", []))
                        else "server"
                    )

                    alert_data = {
                        "id": rule_id,
                        "name": rule_name,
                        "description": getattr(
                            rule, "description", "Metric alert rule"
                        ),
                        "severity": f"Sev{getattr(rule, 'severity', 3)}",
                        "state": "Rule Configured",  # We can't determine actual firing state without alert instances
                        "monitor_condition": "Configured",
                        "fired_time": datetime.now(
                            timezone.utc
                        ).isoformat(),  # Use current time as placeholder
                        "resource_type": "Metric Alert Rule",
                        "target_resource": str(getattr(rule, "scopes", [])),
                        "scope": scope_type,
                        "rule_enabled": getattr(rule, "enabled", False),
                        "window_size": str(getattr(rule, "window_size", "Unknown")),
                        "evaluation_frequency": str(
                            getattr(rule, "evaluation_frequency", "Unknown")
                        ),
                    }

                    # Add criteria information if available
                    if hasattr(rule, "criteria") and rule.criteria:
                        criteria_info = []
                        if hasattr(rule.criteria, "all_of"):
                            for criterion in rule.criteria.all_of:
                                metric_name = getattr(
                                    criterion, "metric_name", "Unknown"
                                )
                                operator = getattr(criterion, "operator", "Unknown")
                                threshold = getattr(criterion, "threshold", "Unknown")
                                criteria_info.append(
                                    f"{metric_name} {operator} {threshold}"
                                )
                        if criteria_info:
                            alert_data["criteria"] = "; ".join(criteria_info)

                    historical_alerts.append(alert_data)
                    logging.info(f"Added metric alert rule: {rule_name} ({scope_type})")

                # If we have alerts management client, try to get actual alert instances
                if self.alerts_client and historical_alerts:
                    try:
                        logging.info(
                            "Attempting to get alert instances from AlertsManagement API"
                        )
                        # Try to get alert instances for the time period
                        alert_instances = self.alerts_client.alerts.get_all(
                            time_range=f"PT{hours_back}H"  # ISO 8601 duration format
                        )

                        instance_count = 0
                        for alert_instance in alert_instances:
                            instance_count += 1
                            # Check if this instance relates to our resources
                            instance_resource = str(
                                getattr(alert_instance, "target_resource", "")
                            )
                            if (
                                database_resource_id in instance_resource
                                or server_resource_id in instance_resource
                            ):
                                # Update or add alert with actual instance data
                                instance_data = self._format_alert(
                                    alert_instance,
                                    "database"
                                    if database_resource_id in instance_resource
                                    else "server",
                                )
                                historical_alerts.append(instance_data)
                                logging.info(
                                    f"Added alert instance: {instance_data.get('name', 'Unknown')}"
                                )

                        logging.info(f"Processed {instance_count} alert instances")
                    except Exception as e:
                        logging.warning(f"Failed to get alert instances: {e}")
                        # Continue with just the rules data

            except Exception as e:
                logging.warning(
                    f"Failed to get metric alert rules, falling back to activity logs: {e}"
                )
                return self._get_alert_history_fallback(
                    resource_group, server_name, database_name, hours_back
                )

            # Sort by fired time, most recent first
            historical_alerts.sort(key=lambda x: x.get("fired_time", ""), reverse=True)

            # Analyze patterns
            analysis = self._analyze_alert_patterns(historical_alerts)

            return {
                "database_resource_id": database_resource_id,
                "server_resource_id": server_resource_id,
                "time_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                    "hours": hours_back,
                },
                "alerts": historical_alerts,
                "total_count": len(historical_alerts),
                "analysis": analysis,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "method": "metric_alerts",
            }

        except Exception as e:
            error_msg = f"Failed to retrieve alert history: {str(e)}"
            logging.error(error_msg, exc_info=True)
            return {"error": error_msg}

    def _get_alert_history_fallback(
        self, resource_group: str, server_name: str, database_name: str, hours_back: int
    ) -> Dict[str, Any]:
        """Fallback method to get alert history using activity logs."""
        try:
            database_resource_id = self._build_database_resource_id(
                resource_group, server_name, database_name
            )
            server_resource_id = self._build_server_resource_id(
                resource_group, server_name
            )

            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=hours_back)

            filter_query = (
                f"eventTimestamp ge '{start_time.isoformat()}' and "
                f"eventTimestamp le '{end_time.isoformat()}' and "
                f"resourceId eq '{database_resource_id}'"
            )

            logging.info(f"Using activity logs fallback with filter: {filter_query}")

            try:
                activity_logs = self.monitor_client.activity_logs.list(
                    filter=filter_query
                )

                alerts = []
                log_count = 0
                for log_entry in activity_logs:
                    log_count += 1

                logging.info(f"Found {log_count} activity log entries")

                # Reset iterator and process entries
                activity_logs = self.monitor_client.activity_logs.list(
                    filter=filter_query
                )
                for log_entry in activity_logs:
                    if hasattr(log_entry, "level") and log_entry.level in [
                        "Warning",
                        "Error",
                        "Critical",
                    ]:
                        alert_data = {
                            "id": getattr(log_entry, "event_data_id", "unknown"),
                            "name": getattr(log_entry, "operation_name", {}).get(
                                "value", "Unknown Operation"
                            ),
                            "description": getattr(
                                log_entry, "description", "No description available"
                            ),
                            "severity": self._map_level_to_severity(
                                getattr(log_entry, "level", "Informational")
                            ),
                            "state": "Resolved",  # Activity logs are historical
                            "monitor_condition": "Resolved",
                            "fired_time": getattr(
                                log_entry, "event_timestamp", end_time
                            ).isoformat(),
                            "resolved_time": getattr(
                                log_entry, "event_timestamp", end_time
                            ).isoformat(),
                            "resource_type": "Activity Log Event",
                            "target_resource": getattr(log_entry, "resource_id", ""),
                            "scope": "server"
                            if server_resource_id
                            in str(getattr(log_entry, "resource_id", ""))
                            else "database",
                            "caller": getattr(log_entry, "caller", "Unknown"),
                            "status": getattr(log_entry, "status", {}).get(
                                "value", "Unknown"
                            ),
                        }
                        alerts.append(alert_data)
                        logging.info(
                            f"Added alert from activity log: {alert_data['name']}"
                        )

                # Sort by fired time, most recent first
                alerts.sort(key=lambda x: x.get("fired_time", ""), reverse=True)

            except Exception as e:
                logging.error(f"Failed to process activity logs: {e}")
                alerts = []

            # Analyze patterns
            analysis = self._analyze_alert_patterns(alerts)

            return {
                "database_resource_id": database_resource_id,
                "server_resource_id": server_resource_id,
                "time_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                    "hours": hours_back,
                },
                "alerts": alerts,
                "total_count": len(alerts),
                "analysis": analysis,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "method": "activity_log_fallback",
            }

        except Exception as e:
            error_msg = f"Failed to retrieve alert history using fallback: {str(e)}"
            logging.error(error_msg, exc_info=True)
            return {"error": error_msg}

    def _format_alert(self, alert, scope: str) -> Dict[str, Any]:
        """Format an alert object into a consistent dictionary structure."""
        try:
            # Handle different alert object types
            if hasattr(alert, "properties"):
                props = alert.properties
                return {
                    "id": getattr(alert, "id", "unknown"),
                    "name": getattr(alert, "name", "Unknown Alert"),
                    "description": getattr(
                        props, "description", "No description available"
                    ),
                    "severity": getattr(props, "severity", "Unknown"),
                    "state": getattr(props, "monitor_condition", "Unknown"),
                    "monitor_condition": getattr(props, "monitor_condition", "Unknown"),
                    "fired_time": getattr(
                        props, "fired_time", datetime.now(timezone.utc)
                    ).isoformat(),
                    "resolved_time": getattr(props, "resolved_time", None),
                    "resource_type": getattr(props, "target_resource_type", "Unknown"),
                    "target_resource": getattr(props, "target_resource", ""),
                    "scope": scope,
                }
            else:
                # Fallback for different alert formats
                return {
                    "id": str(getattr(alert, "id", "unknown")),
                    "name": str(getattr(alert, "name", "Unknown Alert")),
                    "description": str(
                        getattr(alert, "description", "No description available")
                    ),
                    "severity": str(getattr(alert, "severity", "Unknown")),
                    "state": str(getattr(alert, "state", "Unknown")),
                    "monitor_condition": str(
                        getattr(alert, "monitor_condition", "Unknown")
                    ),
                    "fired_time": datetime.now(timezone.utc).isoformat(),
                    "resource_type": "Unknown",
                    "target_resource": str(getattr(alert, "target_resource", "")),
                    "scope": scope,
                }
        except Exception as e:
            logging.warning(f"Failed to format alert: {e}")
            return {
                "id": "unknown",
                "name": "Failed to parse alert",
                "description": f"Error parsing alert: {str(e)}",
                "severity": "Unknown",
                "state": "Unknown",
                "scope": scope,
                "fired_time": datetime.now(timezone.utc).isoformat(),
            }

    def _map_level_to_severity(self, level: str) -> str:
        """Map activity log level to alert severity."""
        level_map = {
            "Critical": "Sev0",
            "Error": "Sev1",
            "Warning": "Sev2",
            "Informational": "Sev3",
            "Verbose": "Sev4",
        }
        return level_map.get(level, "Unknown")

    def _analyze_alert_patterns(self, alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze alert patterns to identify trends and issues."""
        if not alerts:
            return {"message": "No alerts to analyze"}

        # Count by severity
        severity_counts: dict = {}
        state_counts: dict = {}
        scope_counts: dict = {}
        resource_type_counts: dict = {}

        for alert in alerts:
            severity = alert.get("severity", "Unknown")
            state = alert.get("state", "Unknown")
            scope = alert.get("scope", "Unknown")
            resource_type = alert.get("resource_type", "Unknown")

            severity_counts[severity] = severity_counts.get(severity, 0) + 1
            state_counts[state] = state_counts.get(state, 0) + 1
            scope_counts[scope] = scope_counts.get(scope, 0) + 1
            resource_type_counts[resource_type] = (
                resource_type_counts.get(resource_type, 0) + 1
            )

        # Find most frequent alert names
        alert_names: dict = {}
        for alert in alerts:
            name = alert.get("name", "Unknown")
            alert_names[name] = alert_names.get(name, 0) + 1

        most_frequent_alerts = sorted(
            alert_names.items(), key=lambda x: x[1], reverse=True
        )[:5]

        return {
            "total_alerts": len(alerts),
            "severity_breakdown": severity_counts,
            "state_breakdown": state_counts,
            "scope_breakdown": scope_counts,
            "resource_type_breakdown": resource_type_counts,
            "most_frequent_alerts": most_frequent_alerts,
            "analysis_notes": self._generate_analysis_notes(
                severity_counts, most_frequent_alerts
            ),
        }

    def _generate_analysis_notes(
        self, severity_counts: Dict, frequent_alerts: List
    ) -> List[str]:
        """Generate human-readable analysis notes."""
        notes = []

        # Severity analysis
        critical_count = severity_counts.get("Sev0", 0) + severity_counts.get(
            "Critical", 0
        )
        error_count = severity_counts.get("Sev1", 0) + severity_counts.get("Error", 0)

        if critical_count > 0:
            notes.append(f"⚠️ {critical_count} critical severity alerts detected")
        if error_count > 0:
            notes.append(f"🔴 {error_count} error severity alerts detected")

        # Frequent alerts analysis
        if frequent_alerts:
            top_alert = frequent_alerts[0]
            if top_alert[1] > 1:
                notes.append(
                    f"🔁 Most frequent alert: '{top_alert[0]}' ({top_alert[1]} occurrences)"
                )

        if not notes:
            notes.append("✅ Alert analysis looks normal")

        return notes
