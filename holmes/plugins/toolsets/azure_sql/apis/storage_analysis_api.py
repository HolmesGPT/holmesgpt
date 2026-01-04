import logging
from datetime import datetime, timedelta
from typing import Dict, List

from azure.core.credentials import TokenCredential
from azure.monitor.query import MetricsQueryClient

from .azure_sql_api import AzureSQLAPIClient


class StorageAnalysisAPI:
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
                f"Please ensure the service principal is added as a database user with appropriate permissions. "
                f"Original error: {error_str}"
            )
        elif "permission was denied" in error_str.lower():
            return (
                f"Insufficient database permissions - check user access rights. "
                f"Original error: {error_str}"
            )
        elif "login failed" in error_str.lower():
            return (
                f"Database login failed - check authentication credentials and database access permissions. "
                f"Original error: {error_str}"
            )
        else:
            return error_str

    def get_storage_metrics(
        self,
        resource_group: str,
        server_name: str,
        database_name: str,
        hours_back: int = 24,
    ) -> Dict:
        """Get storage-related metrics from Azure Monitor."""
        resource_id = (
            f"subscriptions/{self.subscription_id}/"
            f"resourceGroups/{resource_group}/"
            f"providers/Microsoft.Sql/servers/{server_name}/"
            f"databases/{database_name}"
        )

        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours_back)

        try:
            metrics_data = self.metrics_client.query_resource(
                resource_uri=resource_id,
                metric_names=[
                    "storage_percent",
                    "storage",
                    "allocated_data_storage",
                    "log_write_percent",
                    "tempdb_data_size",
                    "tempdb_log_size",
                    "tempdb_log_used_percent",
                ],
                timespan=(start_time, end_time),
                granularity=timedelta(minutes=15),
                aggregations=["Maximum", "Average", "Minimum"],
            )

            result = {}
            for metric in metrics_data.metrics:
                metric_data = []
                for timeseries in metric.timeseries:
                    for data_point in timeseries.data:
                        metric_data.append(
                            {
                                "timestamp": data_point.timestamp.isoformat(),
                                "maximum": data_point.maximum,
                                "average": data_point.average,
                                "minimum": data_point.minimum,
                            }
                        )
                result[metric.name] = metric_data

            return result

        except Exception as e:
            logging.error(f"Failed to get storage metrics: {str(e)}")
            return {"error": str(e)}

    def get_database_size_details(
        self, server_name: str, database_name: str
    ) -> List[Dict]:
        """Get detailed database size information using DMV."""
        query = """
        SELECT
            DB_NAME() as database_name,
            CASE
                WHEN type_desc = 'ROWS' THEN 'Data'
                WHEN type_desc = 'LOG' THEN 'Log'
                ELSE type_desc
            END as file_type,
            name as logical_name,
            physical_name,
            CAST(size * 8.0 / 1024 AS DECIMAL(10,2)) as size_mb,
            CAST(FILEPROPERTY(name, 'SpaceUsed') * 8.0 / 1024 AS DECIMAL(10,2)) as used_mb,
            CAST((size - FILEPROPERTY(name, 'SpaceUsed')) * 8.0 / 1024 AS DECIMAL(10,2)) as free_mb,
            CAST(FILEPROPERTY(name, 'SpaceUsed') * 100.0 / size AS DECIMAL(5,2)) as used_percent,
            CASE
                WHEN max_size = -1 THEN 'Unlimited'
                WHEN max_size = 268435456 THEN 'Default (2TB)'
                ELSE CAST(max_size * 8.0 / 1024 AS VARCHAR(20)) + ' MB'
            END as max_size,
            is_percent_growth,
            CASE
                WHEN is_percent_growth = 1 THEN CAST(growth AS VARCHAR(10)) + '%'
                ELSE CAST(growth * 8.0 / 1024 AS VARCHAR(20)) + ' MB'
            END as growth_setting,
            state_desc as file_state
        FROM sys.database_files
        ORDER BY type_desc, file_id;
        """

        try:
            return self.sql_api_client.execute_query(server_name, database_name, query)
        except Exception as e:
            logging.error(f"Failed to get database size details: {str(e)}")
            return [{"error": str(e)}]

    def get_storage_summary(self, server_name: str, database_name: str) -> Dict:
        """Get storage summary statistics."""
        query = """
        SELECT
            DB_NAME() as database_name,
            CAST(SUM(CASE WHEN type_desc = 'ROWS' THEN size END) * 8.0 / 1024 AS DECIMAL(10,2)) as total_data_size_mb,
            CAST(SUM(CASE WHEN type_desc = 'ROWS' THEN FILEPROPERTY(name, 'SpaceUsed') END) * 8.0 / 1024 AS DECIMAL(10,2)) as used_data_size_mb,
            CAST(SUM(CASE WHEN type_desc = 'LOG' THEN size END) * 8.0 / 1024 AS DECIMAL(10,2)) as total_log_size_mb,
            CAST(SUM(CASE WHEN type_desc = 'LOG' THEN FILEPROPERTY(name, 'SpaceUsed') END) * 8.0 / 1024 AS DECIMAL(10,2)) as used_log_size_mb,
            CAST((SUM(CASE WHEN type_desc = 'ROWS' THEN size END) +
                  SUM(CASE WHEN type_desc = 'LOG' THEN size END)) * 8.0 / 1024 AS DECIMAL(10,2)) as total_database_size_mb,
            CAST((SUM(CASE WHEN type_desc = 'ROWS' THEN FILEPROPERTY(name, 'SpaceUsed') END) +
                  SUM(CASE WHEN type_desc = 'LOG' THEN FILEPROPERTY(name, 'SpaceUsed') END)) * 8.0 / 1024 AS DECIMAL(10,2)) as total_used_size_mb,
            COUNT(CASE WHEN type_desc = 'ROWS' THEN 1 END) as data_files_count,
            COUNT(CASE WHEN type_desc = 'LOG' THEN 1 END) as log_files_count
        FROM sys.database_files;
        """

        try:
            result = self.sql_api_client.execute_query(
                server_name, database_name, query
            )
            return result[0] if result else {}
        except Exception as e:
            logging.error(f"Failed to get storage summary: {str(e)}")
            return {"error": str(e)}

    def get_table_space_usage(
        self, server_name: str, database_name: str, top_count: int = 20
    ) -> List[Dict]:
        """Get space usage by table/index."""
        query = f"""
        SELECT TOP {top_count}
            SCHEMA_NAME(t.schema_id) as schema_name,
            t.name as table_name,
            i.name as index_name,
            i.type_desc as index_type,
            p.rows as row_count,
            a.total_pages,
            a.used_pages,
            a.data_pages,
            CAST(a.total_pages * 8.0 / 1024 AS DECIMAL(10,2)) as total_space_mb,
            CAST(a.used_pages * 8.0 / 1024 AS DECIMAL(10,2)) as used_space_mb,
            CAST(a.data_pages * 8.0 / 1024 AS DECIMAL(10,2)) as data_space_mb,
            CAST((a.total_pages - a.used_pages) * 8.0 / 1024 AS DECIMAL(10,2)) as unused_space_mb,
            CAST((a.used_pages - a.data_pages) * 8.0 / 1024 AS DECIMAL(10,2)) as index_space_mb
        FROM sys.tables t
        INNER JOIN sys.indexes i ON t.object_id = i.object_id
        INNER JOIN sys.partitions p ON i.object_id = p.object_id AND i.index_id = p.index_id
        INNER JOIN (
            SELECT
                object_id,
                index_id,
                SUM(total_pages) as total_pages,
                SUM(used_pages) as used_pages,
                SUM(data_pages) as data_pages
            FROM sys.allocation_units au
            INNER JOIN sys.partitions p ON
                (au.type IN (1,3) AND au.container_id = p.hobt_id) OR
                (au.type = 2 AND au.container_id = p.partition_id)
            GROUP BY object_id, index_id
        ) a ON i.object_id = a.object_id AND i.index_id = a.index_id
        WHERE t.is_ms_shipped = 0
        ORDER BY a.total_pages DESC;
        """

        try:
            return self.sql_api_client.execute_query(server_name, database_name, query)
        except Exception as e:
            logging.error(f"Failed to get table space usage: {str(e)}")
            return []

    def get_storage_growth_trend(self, server_name: str, database_name: str) -> Dict:
        """Get storage growth trends from backup history."""
        query = """
        WITH BackupSizes AS (
            SELECT
                backup_start_date,
                database_name,
                backup_size,
                compressed_backup_size,
                type as backup_type,
                ROW_NUMBER() OVER (PARTITION BY CONVERT(date, backup_start_date) ORDER BY backup_start_date DESC) as rn
            FROM msdb.dbo.backupset
            WHERE database_name = DB_NAME()
                AND type = 'D'  -- Full backups only
                AND backup_start_date >= DATEADD(day, -30, GETDATE())
        )
        SELECT
            CONVERT(date, backup_start_date) as backup_date,
            database_name,
            CAST(backup_size / 1024.0 / 1024.0 AS DECIMAL(10,2)) as backup_size_mb,
            CAST(compressed_backup_size / 1024.0 / 1024.0 AS DECIMAL(10,2)) as compressed_backup_size_mb,
            CAST((backup_size - compressed_backup_size) * 100.0 / backup_size AS DECIMAL(5,2)) as compression_ratio_percent
        FROM BackupSizes
        WHERE rn = 1  -- One backup per day
        ORDER BY backup_date DESC;
        """

        try:
            results = self.sql_api_client.execute_query(
                server_name, database_name, query
            )

            # Calculate growth trend if we have multiple data points
            if len(results) >= 2:
                oldest = results[-1]
                newest = results[0]

                if oldest["backup_size_mb"] and newest["backup_size_mb"]:
                    growth_mb = newest["backup_size_mb"] - oldest["backup_size_mb"]
                    growth_percent = (growth_mb / oldest["backup_size_mb"]) * 100
                    days_diff = (
                        datetime.strptime(str(newest["backup_date"]), "%Y-%m-%d")
                        - datetime.strptime(str(oldest["backup_date"]), "%Y-%m-%d")
                    ).days

                    return {
                        "backup_history": results,
                        "growth_analysis": {
                            "total_growth_mb": round(growth_mb, 2),
                            "growth_percent": round(growth_percent, 2),
                            "days_analyzed": days_diff,
                            "avg_daily_growth_mb": round(growth_mb / days_diff, 2)
                            if days_diff > 0
                            else 0,
                        },
                    }

            return {"backup_history": results, "growth_analysis": None}

        except Exception as e:
            logging.warning(
                f"Failed to get storage growth trend (backup history may not be available): {str(e)}"
            )
            return {"error": str(e)}

    def get_tempdb_usage(self, server_name: str, database_name: str) -> Dict:
        """Get tempdb usage information."""
        query = """
        SELECT
            'TempDB Usage' as metric_type,
            CAST(SUM(size) * 8.0 / 1024 AS DECIMAL(10,2)) as total_size_mb,
            CAST(SUM(FILEPROPERTY(name, 'SpaceUsed')) * 8.0 / 1024 AS DECIMAL(10,2)) as used_size_mb,
            CAST((SUM(size) - SUM(FILEPROPERTY(name, 'SpaceUsed'))) * 8.0 / 1024 AS DECIMAL(10,2)) as free_size_mb,
            CAST(SUM(FILEPROPERTY(name, 'SpaceUsed')) * 100.0 / SUM(size) AS DECIMAL(5,2)) as used_percent
        FROM tempdb.sys.database_files
        WHERE type_desc = 'ROWS'
        UNION ALL
        SELECT
            'TempDB Log' as metric_type,
            CAST(SUM(size) * 8.0 / 1024 AS DECIMAL(10,2)) as total_size_mb,
            CAST(SUM(FILEPROPERTY(name, 'SpaceUsed')) * 8.0 / 1024 AS DECIMAL(10,2)) as used_size_mb,
            CAST((SUM(size) - SUM(FILEPROPERTY(name, 'SpaceUsed'))) * 8.0 / 1024 AS DECIMAL(10,2)) as free_size_mb,
            CAST(SUM(FILEPROPERTY(name, 'SpaceUsed')) * 100.0 / SUM(size) AS DECIMAL(5,2)) as used_percent
        FROM tempdb.sys.database_files
        WHERE type_desc = 'LOG';
        """

        try:
            results = self.sql_api_client.execute_query(
                server_name, database_name, query
            )
            return {
                row["metric_type"]: {
                    "total_size_mb": row["total_size_mb"],
                    "used_size_mb": row["used_size_mb"],
                    "free_size_mb": row["free_size_mb"],
                    "used_percent": row["used_percent"],
                }
                for row in results
            }
        except Exception as e:
            logging.warning(
                f"Failed to get tempdb usage (may not have permissions): {str(e)}"
            )
            return {"error": str(e)}
