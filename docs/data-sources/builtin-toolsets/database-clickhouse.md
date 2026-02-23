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
- **llm_instructions**: Context about this database

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
