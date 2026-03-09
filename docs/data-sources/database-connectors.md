# Database Connectors

HolmesGPT can connect directly to databases to run read-only SQL queries, list tables, and describe schemas. This lets Holmes investigate slow queries, check index usage, examine table structures, and read data for troubleshooting.

All database connectors use `type: database` and share the same configuration pattern. You can configure multiple database instances side by side, each with a different name.

## Supported Databases

| Database | Connection URL Prefix | Details Page |
|---|---|---|
| PostgreSQL | `postgresql://` | [PostgreSQL](builtin-toolsets/database-postgresql.md) |
| MySQL | `mysql://` | [MySQL](builtin-toolsets/database-mysql.md) |
| MariaDB | `mariadb://` or `mysql://` | [MariaDB](builtin-toolsets/database-mariadb.md) |
| ClickHouse | `clickhouse://` or `clickhouse+http://` | [ClickHouse](builtin-toolsets/database-clickhouse.md) |
| SQL Server | `mssql://` | [SQL Server](builtin-toolsets/database-sqlserver.md) |
| SQLite | `sqlite:///` | [SQLite](builtin-toolsets/database-sqlite.md) |
| Azure SQL Database | Specialized toolset | [Azure SQL Database](builtin-toolsets/azure-sql.md) |
| MongoDB Atlas | Specialized toolset | [MongoDB Atlas](builtin-toolsets/mongodb-atlas.md) |
| MariaDB (MCP) | MCP server | [MariaDB MCP](builtin-toolsets/mariadb-mcp.md) |

## Quick Start

=== "Holmes CLI"

    **~/.holmes/config.yaml:**

    ```yaml
    toolsets:
      prod-postgres:
        type: database
        config:
          connection_url: "postgresql://holmes:password@db.example.com:5432/mydb"
        llm_instructions: "Production PostgreSQL with customer and order data"

      analytics-clickhouse:
        type: database
        config:
          connection_url: "clickhouse://analyst:pass@clickhouse.internal:8123/analytics"
        llm_instructions: "ClickHouse analytics warehouse"
    ```

=== "Holmes Helm Chart"

    ```yaml
    additionalEnvVars:
      - name: POSTGRES_URL
        valueFrom:
          secretKeyRef:
            name: postgres-credentials
            key: url

    toolsets:
      prod-postgres:
        type: database
        config:
          connection_url: "{{ env.POSTGRES_URL }}"
        llm_instructions: "Production PostgreSQL database"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      additionalEnvVars:
        - name: POSTGRES_URL
          valueFrom:
            secretKeyRef:
              name: postgres-credentials
              key: url

      toolsets:
        prod-postgres:
          type: database
          config:
            connection_url: "{{ env.POSTGRES_URL }}"
          llm_instructions: "Production PostgreSQL database"
    ```

## Configuration Options

- **connection_url** (required): Database connection URL (see each database's page for the exact format)
- **read_only** (default: `true`): Only allow SELECT, SHOW, DESCRIBE, EXPLAIN, and WITH statements
- **verify_ssl** (default: `true`): Verify SSL certificates
- **max_rows** (default: `200`): Maximum rows returned per query (1-10000)
- **llm_instructions**: Context about this database instance for the LLM

## Tools Provided

Each configured database instance provides three tools:

- **query**: Execute read-only SQL queries
- **list_tables**: List all tables and views in the database
- **describe_table**: Show column definitions, constraints, and indexes for a table

## Connection URL Formats

```
# PostgreSQL
postgresql://user:password@host:5432/dbname

# MySQL
mysql://user:password@host:3306/dbname

# MariaDB
mariadb://user:password@host:3306/dbname

# ClickHouse (native)
clickhouse://user:password@host:9000/dbname

# ClickHouse (HTTP)
clickhouse+http://user:password@host:8123/dbname

# SQL Server
mssql://user:password@host:1433/dbname

# SQLite (local file)
sqlite:///path/to/database.db
```

## Specialized Database Toolsets

Some databases have dedicated toolsets with features beyond SQL queries:

- **[Azure SQL Database](builtin-toolsets/azure-sql.md)** -- Uses the Azure management API to provide Query Store analysis, performance metrics, connection monitoring, and storage analysis. Use this alongside or instead of the generic `type: database` connector for deeper Azure SQL insights.

- **[MongoDB Atlas](builtin-toolsets/mongodb-atlas.md)** -- Connects to the Atlas Admin API to analyze logs, alerts, events, slow queries, and cluster metrics. This is a separate toolset (not `type: database`) since MongoDB uses a different query model.

- **[MariaDB MCP](builtin-toolsets/mariadb-mcp.md)** -- An MCP-based alternative for MariaDB that provides schema inspection and query capabilities through the MCP protocol.

## Common Use Cases

```
"Why is this query slow on prod-postgres: SELECT * FROM orders WHERE status = 'pending'"
```

```
"Show me the schema for the users table in analytics-clickhouse"
```

```
"List all tables and their sizes in prod-postgres"
```

```
"Check for missing indexes on the orders table"
```
