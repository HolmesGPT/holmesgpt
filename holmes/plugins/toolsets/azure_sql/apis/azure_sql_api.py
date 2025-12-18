from typing import Dict, List
import logging
import struct
from azure.core.credentials import TokenCredential
from azure.mgmt.sql import SqlManagementClient


class AzureSQLAPIClient:
    def __init__(
        self,
        credential: TokenCredential,
        subscription_id: str,
    ):
        self.sql_client = SqlManagementClient(credential, subscription_id)
        self.credential = credential

    def _get_access_token_struct(self) -> bytes:
        """Get access token formatted as struct for Azure SQL Database ODBC authentication."""
        try:
            token = self.credential.get_token("https://database.windows.net/.default")
            if not token or not token.token:
                raise ValueError(
                    "Failed to obtain valid access token from Azure credential"
                )

            # Encode token as UTF-16-LE and pack as struct with length prefix
            token_bytes = token.token.encode("UTF-16-LE")
            token_struct = struct.pack(
                f"<I{len(token_bytes)}s", len(token_bytes), token_bytes
            )
            return token_struct
        except Exception as e:
            logging.error(f"Failed to get access token: {str(e)}")
            raise ConnectionError(f"Azure authentication failed: {str(e)}") from e

    def execute_query(
        self, server_name: str, database_name: str, query: str
    ) -> List[Dict]:
        """Execute a T-SQL query against the Azure SQL database."""
        import pyodbc  # type: ignore

        conn = None
        cursor = None

        # Validate connection parameters to prevent segfault
        if not server_name or not database_name:
            raise ValueError("Server name and database name must be provided")

        # Fall back to Azure AD token authentication only if no SQL credentials
        try:
            access_token_struct = self._get_access_token_struct()

            # Build connection string with access token
            connection_string = (
                f"Driver={{ODBC Driver 18 for SQL Server}};"
                f"Server=tcp:{server_name}.database.windows.net,1433;"
                f"Database={database_name};"
                f"Encrypt=yes;"
                f"TrustServerCertificate=no;"
                f"Connection Timeout=30;"
            )

            logging.info(
                f"Attempting to connect to {server_name}.database.windows.net with Azure AD token"
            )

            # Create connection with properly formatted access token
            SQL_COPT_SS_ACCESS_TOKEN = (
                1256  # This connection option is defined by microsoft in msodbcsql.h
            )
            conn = pyodbc.connect(
                connection_string,
                attrs_before={SQL_COPT_SS_ACCESS_TOKEN: access_token_struct},
                timeout=10,
            )
            cursor = conn.cursor()

            cursor.execute(query)
            columns = [column[0] for column in cursor.description]
            results = []

            for row in cursor.fetchall():
                results.append(dict(zip(columns, row)))

            return results

        except pyodbc.Error as e:
            error_msg = (
                f"ODBC Error connecting to {server_name}.{database_name}: {str(e)}"
            )
            logging.error(error_msg, exc_info=True)
            raise ConnectionError(error_msg) from e
        except Exception as e:
            error_msg = (
                f"Failed to execute query on {server_name}.{database_name}: {str(e)}"
            )
            logging.error(error_msg, exc_info=True)
            raise
        finally:
            # Ensure resources are properly cleaned up
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def get_database_advisors(
        self,
        subscription_id: str,
        resource_group: str,
        server_name: str,
        database_name: str,
    ) -> Dict:
        advisors = self.sql_client.database_advisors.list_by_database(
            resource_group_name=resource_group,
            server_name=server_name,
            database_name=database_name,
        )
        return {"value": [advisor.as_dict() for advisor in advisors]}

    def get_database_recommended_actions(
        self,
        subscription_id: str,
        resource_group: str,
        server_name: str,
        database_name: str,
        advisor_name: str,
    ) -> Dict:
        actions = self.sql_client.database_recommended_actions.list_by_database_advisor(
            resource_group_name=resource_group,
            server_name=server_name,
            database_name=database_name,
            advisor_name=advisor_name,
        )
        return {"value": [action.as_dict() for action in actions]}

    def get_database_usages(
        self,
        subscription_id: str,
        resource_group: str,
        server_name: str,
        database_name: str,
    ) -> Dict:
        usages = self.sql_client.database_usages.list_by_database(
            resource_group_name=resource_group,
            server_name=server_name,
            database_name=database_name,
        )
        return {"value": [usage.as_dict() for usage in usages]}

    def get_database_operations(
        self,
        subscription_id: str,
        resource_group: str,
        server_name: str,
        database_name: str,
    ) -> Dict:
        operations = self.sql_client.database_operations.list_by_database(
            resource_group_name=resource_group,
            server_name=server_name,
            database_name=database_name,
        )
        return {"value": [op.as_dict() for op in operations]}

    def get_database_automatic_tuning(
        self,
        subscription_id: str,
        resource_group: str,
        server_name: str,
        database_name: str,
    ) -> Dict:
        tuning = self.sql_client.database_automatic_tuning.get(
            resource_group_name=resource_group,
            server_name=server_name,
            database_name=database_name,
        )
        return dict(tuning.as_dict())

    def get_top_cpu_queries(
        self,
        subscription_id: str,
        resource_group: str,
        server_name: str,
        database_name: str,
        top_count: int = 15,
        hours_back: int = 2,
    ) -> List[Dict]:
        """Get top CPU consuming queries from Query Store."""
        query = f"""
        SELECT TOP {top_count}
            qt.query_sql_text,
            rs.avg_cpu_time,
            rs.count_executions as execution_count,
            (rs.avg_cpu_time * rs.count_executions) as total_cpu_time,
            rs.max_cpu_time,
            rs.min_cpu_time,
            CAST(rs.last_execution_time as DATETIME2) as last_execution_time,
            rs.avg_duration,
            (rs.avg_duration * rs.count_executions) as total_duration
        FROM sys.query_store_query_text qt
        JOIN sys.query_store_query q ON qt.query_text_id = q.query_text_id
        JOIN sys.query_store_plan p ON q.query_id = p.query_id
        JOIN sys.query_store_runtime_stats rs ON p.plan_id = rs.plan_id
        WHERE rs.last_execution_time > DATEADD(hour, -{hours_back}, GETDATE())
        ORDER BY rs.avg_cpu_time DESC;
        """

        return self.execute_query(server_name, database_name, query)

    def get_slow_queries(
        self,
        subscription_id: str,
        resource_group: str,
        server_name: str,
        database_name: str,
        top_count: int = 15,
        hours_back: int = 2,
    ) -> List[Dict]:
        """Get slow/long-running queries from Query Store."""
        query = f"""
        SELECT TOP {top_count}
            qt.query_sql_text,
            rs.avg_duration,
            rs.count_executions as execution_count,
            (rs.avg_duration * rs.count_executions) as total_duration,
            rs.max_duration,
            rs.min_duration,
            CAST(rs.last_execution_time as DATETIME2) as last_execution_time,
            rs.avg_cpu_time,
            (rs.avg_cpu_time * rs.count_executions) as total_cpu_time
        FROM sys.query_store_query_text qt
        JOIN sys.query_store_query q ON qt.query_text_id = q.query_text_id
        JOIN sys.query_store_plan p ON q.query_id = p.query_id
        JOIN sys.query_store_runtime_stats rs ON p.plan_id = rs.plan_id
        WHERE rs.last_execution_time > DATEADD(hour, -{hours_back}, GETDATE())
        ORDER BY rs.avg_duration DESC;
        """

        return self.execute_query(server_name, database_name, query)

    def get_top_data_io_queries(
        self,
        subscription_id: str,
        resource_group: str,
        server_name: str,
        database_name: str,
        top_count: int = 15,
        hours_back: int = 2,
    ) -> List[Dict]:
        """Get top data I/O consuming queries from Query Store."""
        query = f"""
        SELECT TOP {top_count}
            qt.query_sql_text,
            rs.avg_logical_io_reads as avg_logical_reads,
            rs.count_executions as execution_count,
            (rs.avg_logical_io_reads * rs.count_executions) as total_logical_reads,
            rs.max_logical_io_reads as max_logical_reads,
            rs.min_logical_io_reads as min_logical_reads,
            rs.avg_logical_io_writes as avg_logical_writes,
            (rs.avg_logical_io_writes * rs.count_executions) as total_logical_writes,
            rs.max_logical_io_writes as max_logical_writes,
            rs.min_logical_io_writes as min_logical_writes,
            CAST(rs.last_execution_time as DATETIME2) as last_execution_time,
            rs.avg_cpu_time,
            rs.avg_duration
        FROM sys.query_store_query_text qt
        JOIN sys.query_store_query q ON qt.query_text_id = q.query_text_id
        JOIN sys.query_store_plan p ON q.query_id = p.query_id
        JOIN sys.query_store_runtime_stats rs ON p.plan_id = rs.plan_id
        WHERE rs.last_execution_time > DATEADD(hour, -{hours_back}, GETDATE())
        ORDER BY rs.avg_logical_io_reads DESC;
        """

        return self.execute_query(server_name, database_name, query)

    def get_top_log_io_queries(
        self,
        subscription_id: str,
        resource_group: str,
        server_name: str,
        database_name: str,
        top_count: int = 15,
        hours_back: int = 2,
    ) -> List[Dict]:
        """Get top log I/O consuming queries from Query Store."""
        query = f"""
        SELECT TOP {top_count}
            qt.query_sql_text,
            rs.avg_log_bytes_used,
            rs.count_executions as execution_count,
            (rs.avg_log_bytes_used * rs.count_executions) as total_log_bytes_used,
            rs.max_log_bytes_used,
            rs.min_log_bytes_used,
            CAST(rs.last_execution_time as DATETIME2) as last_execution_time,
            rs.avg_cpu_time,
            rs.avg_duration
        FROM sys.query_store_query_text qt
        JOIN sys.query_store_query q ON qt.query_text_id = q.query_text_id
        JOIN sys.query_store_plan p ON q.query_id = p.query_id
        JOIN sys.query_store_runtime_stats rs ON p.plan_id = rs.plan_id
        WHERE rs.last_execution_time > DATEADD(hour, -{hours_back}, GETDATE())
        ORDER BY rs.avg_log_bytes_used DESC;
        """

        return self.execute_query(server_name, database_name, query)
