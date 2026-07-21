import json
import logging
import os
import re
from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type
from urllib.parse import quote, unquote, urlparse

import requests
from pydantic import ConfigDict, Field, model_validator

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
    ToolsetType,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig

import sqlalchemy

logger = logging.getLogger(__name__)

# SQL statements that are safe for read-only access
_READONLY_PATTERN = re.compile(
    r"^\s*(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN|WITH)\b",
    re.IGNORECASE,
)

# Statements that modify data or schema (prefix check)
_WRITE_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE|MERGE|GRANT|REVOKE|CALL|EXEC)\b",
    re.IGNORECASE,
)

# Write keywords anywhere in the query (catches writable CTEs like
# "WITH cte AS (DELETE FROM users RETURNING *) SELECT * FROM cte")
_WRITE_ANYWHERE_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE|MERGE|GRANT|REVOKE|VACUUM)\b",
    re.IGNORECASE,
)


class DatabaseSubtype(str, Enum):
    MYSQL = "mysql"
    POSTGRESQL = "postgresql"
    MSSQL = "mssql"
    SQLITE = "sqlite"
    CLICKHOUSE = "clickhouse"
    MARIADB = "mariadb"
    UNKNOWN = "unknown"


@dataclass
class DatabaseDriverInfo:
    """Holds the database subtype and the preferred SQLAlchemy driver override."""

    subtype: DatabaseSubtype
    driver: Optional[str]  # SQLAlchemy driver string, None = use URL as-is


# Unified mapping from URL scheme keywords to driver info.
# Detection uses "contains" matching: if the URL scheme contains the key,
# the corresponding driver info is used. Keys are ordered longest-first
# to avoid partial matches (e.g., "mssql" before "sql").
_DATABASE_DRIVERS: Dict[str, DatabaseDriverInfo] = {
    "postgresql": DatabaseDriverInfo(DatabaseSubtype.POSTGRESQL, "postgresql+pg8000"),
    "postgres": DatabaseDriverInfo(DatabaseSubtype.POSTGRESQL, "postgresql+pg8000"),
    "mysql": DatabaseDriverInfo(DatabaseSubtype.MYSQL, "mysql+pymysql"),
    "mariadb": DatabaseDriverInfo(DatabaseSubtype.MARIADB, "mysql+pymysql"),
    "sqlite": DatabaseDriverInfo(DatabaseSubtype.SQLITE, None),
    "mssql": DatabaseDriverInfo(DatabaseSubtype.MSSQL, "mssql+pymssql"),
    "clickhouse": DatabaseDriverInfo(DatabaseSubtype.CLICKHOUSE, None),
}


def _lookup_driver_info(scheme: str) -> Optional[DatabaseDriverInfo]:
    """Find the DatabaseDriverInfo for a URL scheme using contains matching."""
    for key, info in _DATABASE_DRIVERS.items():
        if key in scheme:
            return info
    return None


def _normalise_url(raw_url: str) -> str:
    """Rewrite a connection URL to use a pure-Python driver when possible."""
    parsed = urlparse(raw_url)
    scheme = parsed.scheme  # e.g. "postgresql", "mysql+pymysql", "postgres"

    info = _lookup_driver_info(scheme)
    if info and info.driver and scheme != info.driver:
        return raw_url.replace(scheme, info.driver, 1)

    return raw_url


def _detect_subtype(connection_url: str) -> DatabaseSubtype:
    """Detect the database subtype from a connection URL scheme."""
    parsed = urlparse(connection_url)
    info = _lookup_driver_info(parsed.scheme)
    return info.subtype if info else DatabaseSubtype.UNKNOWN


# Per-subtype icons, copied from the frontend data-source catalog so tool-call
# icons match the catalog card. One DatabaseToolset backs every SQL subtype, so
# the icon is picked from the subtype rather than hardcoded (FRO-190).
_ICON_URL_BASE = "https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos"
_SUBTYPE_ICON_URLS: Dict[DatabaseSubtype, str] = {
    DatabaseSubtype.POSTGRESQL: f"{_ICON_URL_BASE}/postgresql.svg",
    DatabaseSubtype.MYSQL: f"{_ICON_URL_BASE}/mysql-icon.svg",
    DatabaseSubtype.MARIADB: f"{_ICON_URL_BASE}/mariadb-icon.svg",
    DatabaseSubtype.MSSQL: f"{_ICON_URL_BASE}/microsoft-icon.svg",
    DatabaseSubtype.SQLITE: "https://cdn.simpleicons.org/sqlite/003B57",
    DatabaseSubtype.CLICKHOUSE: "https://cdn.simpleicons.org/clickhouse/FFCC01",
}

# Fallback for an unknown subtype; matches the catalog's generic "database/sql".
_DEFAULT_DATABASE_ICON_URL = f"{_ICON_URL_BASE}/mysql-icon.svg"


def _icon_url_for_subtype(subtype: DatabaseSubtype) -> str:
    """Return the catalog icon URL for a database subtype."""
    return _SUBTYPE_ICON_URLS.get(subtype, _DEFAULT_DATABASE_ICON_URL)


def _parse_clickhouse_http_url(
    url: str,
) -> Tuple[str, str, Optional[Tuple[str, str]]]:
    """Parse a clickhouse+http(s)://... URL into (base_url, database, auth).

    Uses ``urllib.parse`` attributes so percent-encoded credentials, IPv6 hosts,
    and user-with-empty-password (e.g. the ClickHouse ``default`` user) are all
    handled correctly. ``database`` is percent-encoded for safe interpolation.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    protocol = "https" if "https" in scheme else "http"
    host = parsed.hostname or "localhost"
    port = parsed.port or (8443 if protocol == "https" else 8123)
    host_repr = f"[{host}]" if ":" in host else host  # IPv6 literal
    base_url = f"{protocol}://{host_repr}:{port}"

    database = quote((parsed.path or "").strip("/") or "default", safe="")

    auth: Optional[Tuple[str, str]] = None
    if parsed.username is not None:
        # urllib does not percent-decode userinfo automatically.
        auth = (unquote(parsed.username), unquote(parsed.password or ""))

    return base_url, database, auth


def _execute_clickhouse_http(
    base_url: str,
    database: str,
    auth: Optional[Tuple[str, str]],
    sql: str,
    effective_limit: int,
    timeout_seconds: int = 60,
    verify_ssl: bool = True,
) -> Dict[str, Any]:
    """Execute a read-only ClickHouse query over HTTP, parsing JSONEachRow.

    The response is streamed line-by-line; iteration stops after
    ``effective_limit`` rows so very large result sets are not buffered in memory.
    """
    params = {"database": database, "default_format": "JSONEachRow"}
    session = requests.Session()
    # Avoid silently picking up credentials from ~/.netrc when auth is None;
    # toolset config is the single source of truth for credentials.
    session.trust_env = False
    try:
        response = session.post(
            base_url,
            params=params,
            data=sql.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
            auth=auth,
            verify=verify_ssl,
            timeout=timeout_seconds,
            stream=True,
        )
    except requests.exceptions.RequestException as e:
        raise ValueError(
            f"ClickHouse connection error on {base_url} (db={database}): {e}. "
            f"SQL: {sql[:500]}"
        ) from e

    with response:
        if response.status_code != 200:
            err_body = (response.text or "")[:2000]
            raise ValueError(
                f"ClickHouse HTTP error {response.status_code} on {base_url} "
                f"(db={database}): {err_body or response.reason}. "
                f"SQL: {sql[:500]}"
            )

        columns: List[str] = []
        rows: List[List[Any]] = []
        truncated = False
        kept = 0
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            if kept >= effective_limit:
                truncated = True
                break
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError as e:
                logger.warning(
                    "ClickHouse JSONEachRow decode error on %s (db=%s): %s. Line: %s",
                    base_url,
                    database,
                    e,
                    raw_line[:200],
                )
                continue
            if not columns:
                columns = list(obj.keys())
            rows.append([obj.get(k) for k in columns])
            kept += 1

    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }


class DatabaseConfig(ToolsetConfig):
    """Configuration for the SQL database toolset.

    Example configuration:
    ```yaml
    orders-rds:
      type: database
      config:
        connection_url: "mysql+pymysql://admin:pass@orders.rds.amazonaws.com:3306/orders"
        read_only: true
        verify_ssl: true
        timeout_seconds: 30
      llm_instructions: "This is the orders database for our e-commerce platform"
    ```
    """

    connection_url: str = Field(
        title="Connection URL",
        description=(
            "SQLAlchemy-compatible database connection URL. "
            "Supported databases: PostgreSQL, MySQL/MariaDB, SQLite, SQL Server. "
            "Pure-Python drivers are used automatically (pg8000, PyMySQL, pymssql)."
        ),
        examples=[
            "postgresql://user:pass@host:5432/db",
            "mysql+pymysql://user:pass@host:3306/db",
        ],
    )

    read_only: bool = Field(
        default=True,
        title="Read-Only Mode",
        description=(
            "When True (default), only SELECT, SHOW, DESCRIBE, EXPLAIN, and WITH statements are allowed. "
            "Set to False to allow write operations (INSERT, UPDATE, DELETE, CREATE, ALTER, etc.). "
            "Warning: Disabling read-only mode grants full database access to the LLM."
        ),
    )

    verify_ssl: bool = Field(
        default=True,
        title="Verify SSL",
        description=(
            "When True (default), verify SSL certificates for database connections. "
            "Set to False for self-signed certificates or development environments. "
            "Required for some managed databases with custom certificates (e.g., RDS with custom CAs)."
        ),
    )

    max_rows: int = Field(
        default=200,
        title="Maximum Rows",
        description=(
            "Maximum number of rows to return from query results. "
            "Limits result size to prevent token overflow. "
            "Default: 200 rows."
        ),
        ge=1,
        le=10000,
    )

    clickhouse_use_http_json: bool = Field(
        default=False,
        title="ClickHouse HTTP JSONEachRow",
        description=(
            "When True and connection_url is ClickHouse, execute read queries via the "
            "ClickHouse HTTP API with default_format=JSONEachRow instead of the "
            "SQLAlchemy clickhouse-sqlalchemy driver (TabSeparatedWithNamesAndTypes). "
            "Use when result sets include DateTime64 with precision beyond microseconds "
            "(e.g. OpenTelemetry logs), which can fail driver TSV parsing with "
            "'unconverted data remains'. Default: False."
        ),
    )

    timeout_seconds: int = Field(
        default=60,
        title="Query Timeout",
        description=(
            "Timeout in seconds for ClickHouse HTTP JSONEachRow queries when "
            "clickhouse_use_http_json is enabled. Not applied to the SQLAlchemy driver path."
        ),
        ge=1,
        le=600,
    )

    @model_validator(mode="after")
    def _validate_clickhouse_http_json(self) -> "DatabaseConfig":
        if not self.clickhouse_use_http_json:
            return self
        if "clickhouse" not in self.connection_url.lower():
            raise ValueError(
                "clickhouse_use_http_json requires a ClickHouse connection_url"
            )
        scheme = urlparse(self.connection_url).scheme.lower()
        if "native" in scheme and "http" not in scheme:
            logger.warning(
                "clickhouse_use_http_json uses the ClickHouse HTTP API (port 8123); "
                "connection_url appears to use the native protocol (%s). "
                "Prefer clickhouse+http:// for this option.",
                scheme,
            )
        return self


class DatabaseToolset(Toolset):
    """Toolset for querying SQL databases via SQLAlchemy.

    By default, provides read-only access to any SQLAlchemy-compatible database.
    Write operations (INSERT, UPDATE, DELETE, DROP, etc.) are blocked unless
    explicitly enabled via the read_only configuration option.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    config_classes: ClassVar[list[Type[DatabaseConfig]]] = [DatabaseConfig]

    def __init__(self, name: str = "database/sql", **kwargs: Any):
        llm_instructions = kwargs.pop("llm_instructions", None)
        enabled = kwargs.pop("enabled", False)
        kwargs.pop("type", None)
        subtype_str = kwargs.pop("subtype", None)

        description = kwargs.pop("description", None)
        if not description:
            if name == "database/sql":
                description = "Query SQL databases (PostgreSQL, MySQL, MariaDB, ClickHouse, SQL Server, SQLite)"
            else:
                description = f"Query {name} database"

        super().__init__(
            name=name,
            enabled=enabled,
            description=description,
            type=ToolsetType.DATABASE,
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/database/",
            # Placeholder; replaced per-subtype by _apply_icon_url().
            icon_url=_DEFAULT_DATABASE_ICON_URL,
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
            **kwargs,
        )
        tool_prefix = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")
        self.tools = [
            DatabaseQuery(self, tool_prefix),
            DatabaseListTables(self, tool_prefix),
            DatabaseDescribeTable(self, tool_prefix),
        ]
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

        # Resolve subtype: explicit config > auto-detect later from connection URL.
        # Unknown subtypes are user typos with no legitimate interpretation —
        # raise so they surface as a visible failed toolset in the UI rather
        # than silently falling back to UNKNOWN (which would then auto-detect
        # from the URL, making the typo completely invisible).
        self._subtype: DatabaseSubtype = DatabaseSubtype.UNKNOWN
        if subtype_str:
            try:
                self._subtype = DatabaseSubtype(subtype_str)
            except ValueError as exc:
                valid = ", ".join(s.value for s in DatabaseSubtype)
                raise ValueError(
                    f"Unknown database subtype '{subtype_str}'. "
                    f"Valid values: {valid}. "
                    "Omit `subtype` to auto-detect from the connection URL."
                ) from exc

        # Set initial meta — updated with detected subtype in prerequisites_callable
        self.meta = {"type": "database", "subtype": self._subtype.value}

        # Apply icon for the explicit subtype (or the default until detection).
        self._apply_icon_url()

        self._user_llm_instructions = llm_instructions
        self._dialect: Optional[str] = None
        if self._user_llm_instructions:
            self.llm_instructions = (
                (self.llm_instructions or "")
                + "\n\n## Database-Specific Instructions\n\n"
                + self._user_llm_instructions
            )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.config = DatabaseConfig(**config)
            # Auto-detect subtype from connection URL if not explicitly set
            if self._subtype == DatabaseSubtype.UNKNOWN:
                self._subtype = _detect_subtype(self.database_config.connection_url)
            self.meta = {"type": "database", "subtype": self._subtype.value}
            # Re-apply now the subtype is known (covers auto-detect and lazy init).
            self._apply_icon_url()
            return self._perform_health_check()
        except Exception as e:
            return False, f"Invalid database configuration: {e}"

    def _apply_icon_url(self) -> None:
        """Set the toolset and per-tool icon from the resolved subtype.

        Tools are updated directly (not just the toolset) so the correct icon is
        stamped onto results even under lazy init, where ToolExecutor has already
        registered the tools and won't re-copy the toolset icon.
        """
        icon_url = _icon_url_for_subtype(self._subtype)
        self.icon_url = icon_url
        for tool in self.tools:
            tool.icon_url = icon_url

    def _perform_health_check(self) -> Tuple[bool, str]:
        try:
            if self.database_config.clickhouse_use_http_json:
                base_url, database, auth = _parse_clickhouse_http_url(
                    self.database_config.connection_url
                )
                _execute_clickhouse_http(
                    base_url,
                    database,
                    auth,
                    "SELECT 1",
                    effective_limit=1,
                    timeout_seconds=self.database_config.timeout_seconds,
                    verify_ssl=self.database_config.verify_ssl,
                )
                self._dialect = "clickhouse"
                self._update_tool_descriptions()
                return True, "Connected to ClickHouse (HTTP JSONEachRow)"
            url = _normalise_url(self.database_config.connection_url)
            engine = self._create_engine(url)
            with engine.connect() as conn:
                conn.execute(sqlalchemy.text("SELECT 1"))
            self._dialect = engine.dialect.name
            engine.dispose()
            self._update_tool_descriptions()
            return True, f"Connected to {self._dialect} database"
        except Exception as e:
            return False, f"Database connection failed: {e}"

    def _update_tool_descriptions(self) -> None:
        for tool in self.tools:
            if isinstance(tool, DatabaseQuery):
                tool.description = (
                    f"Execute a {self._dialect} SQL query against the database. "
                    "In read-only mode (default), only SELECT, SHOW, DESCRIBE, EXPLAIN, "
                    "and WITH (CTE) statements are allowed. Write operations can be enabled via configuration. "
                    "Returns up to 200 rows."
                )
                tool.parameters["sql"].description = (
                    f"The {self._dialect} SQL query to execute. Must be a read-only statement "
                    "(SELECT, SHOW, DESCRIBE, EXPLAIN, WITH). "
                    "Always limit the number of rows returned using the appropriate syntax for the database."
                )

    def _create_engine(self, url: str):
        connect_args = {}

        if not self.database_config.verify_ssl:
            if "postgresql" in url:
                # pg8000 uses ssl_context parameter
                connect_args["ssl_context"] = None
            elif "mysql" in url or "pymysql" in url:
                connect_args["ssl_disabled"] = True
            elif "clickhouse" in url:
                connect_args["verify"] = False
            elif "mssql" in url or "pymssql" in url:
                connect_args["TrustServerCertificate"] = "yes"

        return sqlalchemy.create_engine(
            url, pool_pre_ping=True, connect_args=connect_args
        )

    @property
    def database_config(self) -> DatabaseConfig:
        return self.config  # type: ignore

    def execute_query(self, sql: str, limit: Optional[int] = None) -> Dict[str, Any]:
        """Execute a SQL query and return results as a dict.

        Returns:
            Dict with keys: columns, rows, row_count, truncated
        """
        if self.database_config.read_only:
            if _WRITE_PATTERN.match(sql):
                raise ValueError(
                    f"Write operations are not allowed. "
                    f"Only SELECT, SHOW, DESCRIBE, EXPLAIN, and WITH statements are permitted. "
                    f"Received: {sql[:80]}"
                )

            if _WRITE_ANYWHERE_PATTERN.search(sql):
                raise ValueError(
                    f"Write operations are not allowed anywhere in the query. "
                    f"Only SELECT, SHOW, DESCRIBE, EXPLAIN, and WITH statements are permitted. "
                    f"Received: {sql[:80]}"
                )

            if not _READONLY_PATTERN.match(sql):
                raise ValueError(
                    f"Only SELECT, SHOW, DESCRIBE, EXPLAIN, and WITH statements are permitted. "
                    f"Received: {sql[:80]}"
                )

        effective_limit = min(
            limit or self.database_config.max_rows, self.database_config.max_rows
        )

        if self.database_config.clickhouse_use_http_json:
            logger.debug(
                "ClickHouse HTTP JSONEachRow query: %s",
                sql[:80],
            )
            base_url, database, auth = _parse_clickhouse_http_url(
                self.database_config.connection_url
            )
            return _execute_clickhouse_http(
                base_url,
                database,
                auth,
                sql,
                effective_limit,
                timeout_seconds=self.database_config.timeout_seconds,
                verify_ssl=self.database_config.verify_ssl,
            )

        url = _normalise_url(self.database_config.connection_url)
        engine = self._create_engine(url)
        try:
            with engine.connect() as conn:
                if self.database_config.read_only:
                    try:
                        conn.execute(sqlalchemy.text("SET TRANSACTION READ ONLY"))
                    except Exception:
                        pass  # Not all dialects support this; regex check is primary guard
                result = conn.execute(sqlalchemy.text(sql))

                # Check if the result returns rows (SELECT, SHOW, etc.) or not (INSERT, UPDATE, etc.)
                if result.returns_rows:
                    columns = list(result.keys())
                    rows: List[List[Any]] = []
                    truncated = False
                    for i, row in enumerate(result):
                        if i >= effective_limit:
                            truncated = True
                            break
                        rows.append([_serialize_value(v) for v in row])

                    return {
                        "columns": columns,
                        "rows": rows,
                        "row_count": len(rows),
                        "truncated": truncated,
                    }
                else:
                    # Write operations don't return rows
                    return {
                        "columns": [],
                        "rows": [],
                        "row_count": 0,
                        "truncated": False,
                        "rows_affected": result.rowcount
                        if result.rowcount >= 0
                        else None,
                    }
        finally:
            engine.dispose()


def _serialize_value(val: Any) -> Any:
    """Convert database values to JSON-safe types."""
    if val is None:
        return None
    if isinstance(val, (int, float, bool, str)):
        return val
    if isinstance(val, bytes):
        return val.hex()
    if isinstance(val, (dict, list)):
        return val
    # datetime, Decimal, UUID, etc.
    return str(val)


class BaseDatabaseTool(Tool, ABC):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, toolset: DatabaseToolset, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._toolset = toolset


class DatabaseQuery(BaseDatabaseTool):
    """Execute a SQL query against the connected database."""

    def __init__(self, toolset: DatabaseToolset, tool_prefix: str):
        super().__init__(
            toolset=toolset,
            name=f"{tool_prefix}_query",
            description=(
                "Execute a SQL query against the database. "
                "In read-only mode (default), only SELECT, SHOW, DESCRIBE, EXPLAIN, "
                "and WITH (CTE) statements are allowed. Write operations can be enabled via configuration. "
                "Returns up to 200 rows. Always use LIMIT to control result size."
            ),
            parameters={
                "sql": ToolParameter(
                    description=(
                        "The SQL query to execute. Must be a read-only statement "
                        "(SELECT, SHOW, DESCRIBE, EXPLAIN, WITH). "
                        "Use LIMIT to control result size. "
                        "Example: SELECT * FROM users WHERE created_at > '2024-01-01' LIMIT 50"
                    ),
                    type="string",
                    required=True,
                ),
                "limit": ToolParameter(
                    description="Maximum number of rows to return (default: 200, max: 200)",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        sql = params["sql"]
        limit = params.get("limit")

        try:
            data = self._toolset.execute_query(sql, limit=limit)
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )
        except Exception as e:
            error_msg = str(e)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Query failed: {error_msg}. SQL: {sql[:200]}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        sql = params.get("sql", "")
        short = sql[:60].replace("\n", " ")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: {short}"


class DatabaseListTables(BaseDatabaseTool):
    """List tables in the connected database."""

    def __init__(self, toolset: DatabaseToolset, tool_prefix: str):
        super().__init__(
            toolset=toolset,
            name=f"{tool_prefix}_list_tables",
            description=(
                "List all tables (and optionally views) in the database. "
                "Use schema parameter to filter by schema."
            ),
            parameters={
                "schema": ToolParameter(
                    description=(
                        "Schema to list tables from. Defaults to the database default schema "
                        "(e.g. 'public' for PostgreSQL)."
                    ),
                    type="string",
                    required=False,
                ),
                "include_views": ToolParameter(
                    description="Include views in the listing (default: true)",
                    type="boolean",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            schema = params.get("schema")
            include_views = params.get("include_views", True)

            url = _normalise_url(self._toolset.database_config.connection_url)
            engine = self._toolset._create_engine(url)
            try:
                inspector = sqlalchemy.inspect(engine)
                tables = inspector.get_table_names(schema=schema)
                result: Dict[str, Any] = {"tables": sorted(tables)}

                if include_views:
                    views = inspector.get_view_names(schema=schema)
                    result["views"] = sorted(views)

                result["total_count"] = len(tables) + (
                    len(views) if include_views else 0
                )
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=result,
                    params=params,
                )
            finally:
                engine.dispose()

        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to list tables (schema={params.get('schema', 'default')}): {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        schema = params.get("schema", "default")
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: List tables in {schema}"
        )


class DatabaseDescribeTable(BaseDatabaseTool):
    """Describe the schema of a specific table."""

    def __init__(self, toolset: DatabaseToolset, tool_prefix: str):
        super().__init__(
            toolset=toolset,
            name=f"{tool_prefix}_describe_table",
            description=(
                "Get the column definitions and constraints for a table. "
                "Shows column names, types, nullability, defaults, primary keys, "
                "foreign keys, and indexes."
            ),
            parameters={
                "table_name": ToolParameter(
                    description="Name of the table to describe",
                    type="string",
                    required=True,
                ),
                "schema": ToolParameter(
                    description="Schema the table belongs to (optional)",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            table_name = params["table_name"]
            schema = params.get("schema")

            url = _normalise_url(self._toolset.database_config.connection_url)
            engine = self._toolset._create_engine(url)
            try:
                inspector = sqlalchemy.inspect(engine)

                columns = inspector.get_columns(table_name, schema=schema)
                pk = inspector.get_pk_constraint(table_name, schema=schema)
                fks = inspector.get_foreign_keys(table_name, schema=schema)
                indexes = inspector.get_indexes(table_name, schema=schema)

                col_info = []
                for col in columns:
                    col_info.append(
                        {
                            "name": col["name"],
                            "type": str(col["type"]),
                            "nullable": col.get("nullable", True),
                            "default": str(col["default"])
                            if col.get("default")
                            else None,
                        }
                    )

                fk_info = []
                for fk in fks:
                    fk_info.append(
                        {
                            "constrained_columns": fk.get("constrained_columns", []),
                            "referred_table": fk.get("referred_table"),
                            "referred_columns": fk.get("referred_columns", []),
                        }
                    )

                idx_info = []
                for idx in indexes:
                    idx_info.append(
                        {
                            "name": idx.get("name"),
                            "columns": idx.get("column_names", []),
                            "unique": idx.get("unique", False),
                        }
                    )

                result = {
                    "table_name": table_name,
                    "schema": schema,
                    "columns": col_info,
                    "primary_key": pk.get("constrained_columns", []) if pk else [],
                    "foreign_keys": fk_info,
                    "indexes": idx_info,
                }

                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=result,
                    params=params,
                )
            finally:
                engine.dispose()

        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to describe table '{params.get('table_name')}': {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        table = params.get("table_name", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Describe {table}"
