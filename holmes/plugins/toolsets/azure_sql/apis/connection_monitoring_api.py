import logging
from datetime import datetime, timedelta
from typing import Dict, List

from azure.core.credentials import TokenCredential
from azure.monitor.query import MetricsQueryClient

from .azure_sql_api import AzureSQLAPIClient


class ConnectionMonitoringAPI:
    def __init__(
        self,
        credential: TokenCredential,
        subscription_id: str,
    ):
        self.sql_api_client = AzureSQLAPIClient(credential, subscription_id)
        self.metrics_client = MetricsQueryClient(credential)
        self.subscription_id = subscription_id

    def _format_sql_error(self, error: Exception) -> str:
        """Format SQL errors with helpful permission guidance."""
        error_str = str(error)

        # Detect common permission issues
        if (
            "Login failed for user" in error_str
            and "token-identified principal" in error_str
        ):
            return (
                f"Azure AD authentication failed - the service principal lacks database permissions. "
                f"Please ensure the service principal is added as a database user with VIEW SERVER STATE permission. "
                f"Original error: {error_str}"
            )
        elif (
            "permission was denied" in error_str.lower()
            or "view server state" in error_str.lower()
        ):
            return (
                f"Insufficient database permissions - the user needs VIEW SERVER STATE permission to access system views. "
                f"Original error: {error_str}"
            )
        elif "login failed" in error_str.lower():
            return (
                f"Database login failed - check authentication credentials and database access permissions. "
                f"Original error: {error_str}"
            )
        else:
            return error_str

    def get_connection_metrics(
        self,
        resource_group: str,
        server_name: str,
        database_name: str,
        hours_back: int = 2,
    ) -> Dict:
        """Get connection-related metrics from Azure Monitor."""
        resource_id = (
            f"subscriptions/{self.subscription_id}/"
            f"resourceGroups/{resource_group}/"
            f"providers/Microsoft.Sql/servers/{server_name}/"
            f"databases/{database_name}"
        )

        end_time = datetime.now()
        # Use longer timespan for better data availability
        start_time = end_time - timedelta(hours=max(hours_back, 24))

        try:
            metrics_data = self.metrics_client.query_resource(
                resource_uri=resource_id,
                metric_names=[
                    "connection_successful",  # This exists
                    "sessions_count",  # This exists
                    "cpu_percent",  # This exists
                    "storage_percent",  # This exists
                ],
                timespan=(start_time, end_time),
                granularity=timedelta(hours=1),  # Larger granularity for better data
                aggregations=["Maximum", "Average", "Total"],
            )

            result = {}
            for metric in metrics_data.metrics:
                metric_data = []
                for timeseries in metric.timeseries:
                    for data_point in timeseries.data:
                        # Handle None values and pick the best available aggregation
                        value_data = {
                            "timestamp": data_point.timestamp.isoformat(),
                            "maximum": data_point.maximum
                            if data_point.maximum is not None
                            else 0,
                            "average": data_point.average
                            if data_point.average is not None
                            else 0,
                            "total": data_point.total
                            if data_point.total is not None
                            else 0,
                        }
                        metric_data.append(value_data)
                result[metric.name] = metric_data

            return result

        except Exception as e:
            logging.error(f"Failed to get connection metrics: {str(e)}")
            return {"error": str(e)}

    def get_active_connections(
        self, server_name: str, database_name: str
    ) -> List[Dict]:
        """Get currently active connections using DMV."""
        query = """
        SELECT
            s.session_id,
            s.login_name,
            s.host_name,
            s.program_name,
            s.login_time,
            s.last_request_start_time,
            s.last_request_end_time,
            s.status,
            s.cpu_time,
            s.memory_usage,
            s.total_scheduled_time,
            s.total_elapsed_time,
            s.reads,
            s.writes,
            s.logical_reads,
            CASE
                WHEN r.session_id IS NOT NULL THEN 'Active'
                ELSE 'Inactive'
            END as connection_status,
            r.blocking_session_id,
            r.wait_type,
            r.wait_time,
            r.wait_resource
        FROM sys.dm_exec_sessions s
        LEFT JOIN sys.dm_exec_requests r ON s.session_id = r.session_id
        WHERE s.is_user_process = 1
        ORDER BY s.login_time DESC;
        """

        try:
            return self.sql_api_client.execute_query(server_name, database_name, query)
        except Exception as e:
            formatted_error = self._format_sql_error(e)
            logging.error(f"Failed to get active connections: {formatted_error}")
            return []

    def get_connection_summary(self, server_name: str, database_name: str) -> Dict:
        """Get connection summary statistics."""
        query = """
        SELECT
            COUNT(*) as total_connections,
            COUNT(CASE WHEN r.session_id IS NOT NULL THEN 1 END) as active_connections,
            COUNT(CASE WHEN r.session_id IS NULL THEN 1 END) as idle_connections,
            COUNT(CASE WHEN r.blocking_session_id > 0 THEN 1 END) as blocked_connections,
            COUNT(DISTINCT s.login_name) as unique_users,
            COUNT(DISTINCT s.host_name) as unique_hosts,
            MAX(s.login_time) as latest_login,
            MIN(s.login_time) as earliest_login
        FROM sys.dm_exec_sessions s
        LEFT JOIN sys.dm_exec_requests r ON s.session_id = r.session_id
        WHERE s.is_user_process = 1;
        """

        try:
            result = self.sql_api_client.execute_query(
                server_name, database_name, query
            )
            return result[0] if result else {}
        except Exception as e:
            formatted_error = self._format_sql_error(e)
            logging.error(f"Failed to get connection summary: {formatted_error}")
            return {"error": formatted_error}

    def get_failed_connections(
        self, server_name: str, database_name: str, hours_back: int = 24
    ) -> List[Dict]:
        """Get failed connection attempts from extended events or system health."""
        # Note: This query looks for connectivity ring buffer events
        query = f"""
        WITH ConnectivityEvents AS (
            SELECT
                CAST(event_data AS XML) as event_xml,
                timestamp_utc
            FROM sys.fn_xe_file_target_read_file('system_health*.xel', null, null, null)
            WHERE object_name = 'connectivity_ring_buffer_recorded'
            AND timestamp_utc > DATEADD(hour, -{hours_back}, GETUTCDATE())
        )
        SELECT TOP 100
            timestamp_utc,
            event_xml.value('(/Record/ConnectivityTraceRecord/RecordType)[1]', 'varchar(50)') as record_type,
            event_xml.value('(/Record/ConnectivityTraceRecord/RecordSource)[1]', 'varchar(50)') as record_source,
            event_xml.value('(/Record/ConnectivityTraceRecord/Spid)[1]', 'int') as spid,
            event_xml.value('(/Record/ConnectivityTraceRecord/SniConsumerError)[1]', 'int') as sni_consumer_error,
            event_xml.value('(/Record/ConnectivityTraceRecord/State)[1]', 'int') as state,
            event_xml.value('(/Record/ConnectivityTraceRecord/RemoteHost)[1]', 'varchar(100)') as remote_host,
            event_xml.value('(/Record/ConnectivityTraceRecord/RemotePort)[1]', 'varchar(10)') as remote_port
        FROM ConnectivityEvents
        WHERE event_xml.value('(/Record/ConnectivityTraceRecord/RecordType)[1]', 'varchar(50)') LIKE '%Error%'
        ORDER BY timestamp_utc DESC;
        """

        try:
            return self.sql_api_client.execute_query(server_name, database_name, query)
        except Exception as e:
            logging.warning(
                f"Failed to get failed connections (extended events may not be available): {str(e)}"
            )
            # Fallback to a simpler approach using error log if available
            return []

    def get_connection_pool_stats(self, server_name: str, database_name: str) -> Dict:
        """Get connection pool related statistics."""
        query = """
        SELECT
            'Database Connections' as metric_name,
            COUNT(*) as current_value,
            'connections' as unit
        FROM sys.dm_exec_sessions
        WHERE is_user_process = 1
        UNION ALL
        SELECT
            'Active Requests' as metric_name,
            COUNT(*) as current_value,
            'requests' as unit
        FROM sys.dm_exec_requests
        WHERE session_id > 50
        UNION ALL
        SELECT
            'Waiting Tasks' as metric_name,
            COUNT(*) as current_value,
            'tasks' as unit
        FROM sys.dm_os_waiting_tasks
        WHERE session_id > 50;
        """

        try:
            results = self.sql_api_client.execute_query(
                server_name, database_name, query
            )
            return {
                row["metric_name"]: {"value": row["current_value"], "unit": row["unit"]}
                for row in results
            }
        except Exception as e:
            formatted_error = self._format_sql_error(e)
            logging.error(f"Failed to get connection pool stats: {formatted_error}")
            return {"error": formatted_error}
