# ClickHouse

Connect HolmesGPT to ClickHouse databases to analyze OLAP query performance, investigate slow aggregations, check table compression, examine cluster health, and read data for troubleshooting.

You can configure multiple ClickHouse instances with different names (e.g., `clickhouse-analytics`, `clickhouse-metrics`, `clickhouse-staging`).

## Creating a Read-Only User

```sql
-- Create user
CREATE USER holmes_readonly IDENTIFIED BY 'your_secure_password';

-- Grant read-only access to specific database
GRANT SELECT ON your_database.* TO holmes_readonly;

-- Grant access to system tables for performance analysis
GRANT SELECT ON system.* TO holmes_readonly;
GRANT SELECT ON information_schema.* TO holmes_readonly;
```

**For all databases:**
```sql
CREATE USER holmes_readonly IDENTIFIED BY 'your_secure_password';
GRANT SELECT ON *.* TO holmes_readonly;
GRANT SELECT ON system.* TO holmes_readonly;
```

## Configuration

=== "Holmes CLI"

    **~/.holmes/config.yaml:**

    ```yaml
    toolsets:
      clickhouse-analytics:
        type: database
        config:
          connection_url: "clickhouse://holmes_readonly:your_secure_password@clickhouse.example.com:9000/metrics"
        llm_instructions: "ClickHouse analytics warehouse with event streams and metrics"

      clickhouse-logs:
        type: database
        config:
          connection_url: "clickhouse+http://log_reader:pass@clickhouse-logs.internal:8123/logs"
          clickhouse_use_http_json: true
        llm_instructions: "Log analytics database with application and system logs"
    ```

    **Using environment variables:**

    ```yaml
    toolsets:
      clickhouse-analytics:
        type: database
        config:
          connection_url: "{{ env.CLICKHOUSE_URL }}"
    ```

    **Connection URL format:**
    ```
    clickhouse://[username]:[password]@[host]:[port]/[database]
    clickhouse+http://[username]:[password]@[host]:[port]/[database]
    ```

    Note: Use native protocol (port 9000) or HTTP interface (port 8123).

=== "Holmes Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic clickhouse-credentials \
      --from-literal=url='clickhouse://holmes_readonly:your_secure_password@clickhouse.example.com:9000/metrics' \
      -n holmes
    ```

    **Step 2: Configure in values.yaml**

    ```yaml
    additionalEnvVars:
      - name: CLICKHOUSE_URL
        valueFrom:
          secretKeyRef:
            name: clickhouse-credentials
            key: url

    toolsets:
      clickhouse-analytics:
        type: database
        config:
          connection_url: "{{ env.CLICKHOUSE_URL }}"
        llm_instructions: "ClickHouse analytics warehouse with event streams and metrics"
    ```

    **Multiple instances:**

    ```yaml
    additionalEnvVars:
      - name: CLICKHOUSE_ANALYTICS_URL
        valueFrom:
          secretKeyRef:
            name: clickhouse-analytics
            key: url
      - name: CLICKHOUSE_LOGS_URL
        valueFrom:
          secretKeyRef:
            name: clickhouse-logs
            key: url

    toolsets:
      clickhouse-analytics:
        type: database
        config:
          connection_url: "{{ env.CLICKHOUSE_ANALYTICS_URL }}"

      clickhouse-logs:
        type: database
        config:
          connection_url: "{{ env.CLICKHOUSE_LOGS_URL }}"
    ```

=== "Robusta Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic clickhouse-credentials \
      --from-literal=url='clickhouse://holmes_readonly:your_secure_password@clickhouse.example.com:9000/metrics' \
      -n default
    ```

    **Step 2: Configure in values.yaml**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: CLICKHOUSE_URL
          valueFrom:
            secretKeyRef:
              name: clickhouse-credentials
              key: url

      toolsets:
        clickhouse-analytics:
          type: database
          config:
            connection_url: "{{ env.CLICKHOUSE_URL }}"
          llm_instructions: "ClickHouse analytics warehouse with event streams and metrics"
    ```

    **Multiple instances:**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: CLICKHOUSE_ANALYTICS_URL
          valueFrom:
            secretKeyRef:
              name: clickhouse-analytics
              key: url
        - name: CLICKHOUSE_LOGS_URL
          valueFrom:
            secretKeyRef:
              name: clickhouse-logs
              key: url

      toolsets:
        clickhouse-analytics:
          type: database
          config:
            connection_url: "{{ env.CLICKHOUSE_ANALYTICS_URL }}"

        clickhouse-logs:
          type: database
          config:
            connection_url: "{{ env.CLICKHOUSE_LOGS_URL }}"
    ```

## Configuration Options

- **connection_url** (required): ClickHouse connection URL
- **read_only** (default: `true`): Only allow SELECT/SHOW/DESCRIBE/EXPLAIN/WITH statements
- **verify_ssl** (default: `true`): Verify SSL certificates
- **max_rows** (default: `200`): Maximum rows to return (1-10000)
- **timeout_seconds** (default: `60`): HTTP JSONEachRow query timeout in seconds (1-600); used when `clickhouse_use_http_json` is enabled
- **clickhouse_use_http_json** (default: `false`): Use ClickHouse HTTP API with `JSONEachRow` instead of the SQLAlchemy driver's TSV format for query results
- **llm_instructions**: Context about this database

### HTTP JSONEachRow mode (`clickhouse_use_http_json`)

The default SQLAlchemy HTTP driver returns `TabSeparatedWithNamesAndTypes` and parses `DateTime64` timestamps with Python `strptime` using microsecond precision (`%f`). Result sets with **nanosecond** timestamps (common in OpenTelemetry log tables, e.g. `DateTime64(9)`) can fail while reading rows with:

```text
ValueError: unconverted data remains: 789
```

Enable JSONEachRow when you query tables that return high-precision `DateTime64` columns:

```yaml
toolsets:
  clickhouse-otel-logs:
    type: database
    config:
      connection_url: "clickhouse+http://user:pass@clickhouse:8123/otel"
      clickhouse_use_http_json: true
      read_only: true
      max_rows: 200
```

This path only affects **query execution** (`execute_query`); list/describe tools still use SQLAlchemy. Use `clickhouse+http://` (port 8123), not the native protocol, with this option.

## Common Use Cases

```
"Analyze query performance: SELECT count() FROM events WHERE date >= today() - 30"
```

```
"Show table sizes and compression ratios"
```

```
"Check for inefficient queries scanning too many rows"
```
