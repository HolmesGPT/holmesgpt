# PostgreSQL

Connect HolmesGPT to PostgreSQL databases to analyze query performance, investigate slow queries, check index usage, examine database health, and read data for troubleshooting.

You can configure multiple PostgreSQL instances with different names (e.g., `prod-db`, `analytics-db`, `staging-db`).

## Creating a Read-Only User

```sql
-- Create user
CREATE USER holmes_readonly WITH PASSWORD 'your_secure_password';

-- Grant connection
GRANT CONNECT ON DATABASE your_database TO holmes_readonly;

-- Connect to database
\c your_database

-- Grant schema access
GRANT USAGE ON SCHEMA public TO holmes_readonly;

-- Grant read access to tables
GRANT SELECT ON ALL TABLES IN SCHEMA public TO holmes_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO holmes_readonly;

-- Optional: Grant access to pg_stat views for performance analysis
GRANT pg_read_all_stats TO holmes_readonly;
```

## Configuration

=== "Holmes CLI"

    **~/.holmes/config.yaml:**

    ```yaml
    toolsets:
      prod-postgres:
        type: database
        config:
          connection_url: "postgresql://holmes_readonly:your_secure_password@postgres.example.com:5432/mydb"
        llm_instructions: "Production PostgreSQL with customer and order data"

      analytics-postgres:
        type: database
        config:
          connection_url: "postgresql://analyst:pass@analytics-pg.internal:5432/analytics"
        llm_instructions: "Analytics warehouse for reporting queries"
    ```

    **Using environment variables:**

    ```yaml
    toolsets:
      prod-postgres:
        type: database
        config:
          connection_url: "{{ env.POSTGRES_URL }}"
    ```

    **Connection URL format:**
    ```
    postgresql://[username]:[password]@[host]:[port]/[database]
    ```

=== "Holmes Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic postgres-credentials \
      --from-literal=url='postgresql://holmes_readonly:your_secure_password@postgres.example.com:5432/mydb' \
      -n holmes
    ```

    **Step 2: Configure in values.yaml**

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

    **Multiple instances:**

    ```yaml
    additionalEnvVars:
      - name: PROD_POSTGRES_URL
        valueFrom:
          secretKeyRef:
            name: postgres-prod
            key: url
      - name: ANALYTICS_POSTGRES_URL
        valueFrom:
          secretKeyRef:
            name: postgres-analytics
            key: url

    toolsets:
      prod-postgres:
        type: database
        config:
          connection_url: "{{ env.PROD_POSTGRES_URL }}"

      analytics-postgres:
        type: database
        config:
          connection_url: "{{ env.ANALYTICS_POSTGRES_URL }}"
    ```

=== "Robusta Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic postgres-credentials \
      --from-literal=url='postgresql://holmes_readonly:your_secure_password@postgres.example.com:5432/mydb' \
      -n default
    ```

    **Step 2: Configure in values.yaml**

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

    **Multiple instances:**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: PROD_POSTGRES_URL
          valueFrom:
            secretKeyRef:
              name: postgres-prod
              key: url
        - name: ANALYTICS_POSTGRES_URL
          valueFrom:
            secretKeyRef:
              name: postgres-analytics
              key: url

      toolsets:
        prod-postgres:
          type: database
          config:
            connection_url: "{{ env.PROD_POSTGRES_URL }}"

        analytics-postgres:
          type: database
          config:
            connection_url: "{{ env.ANALYTICS_POSTGRES_URL }}"
    ```

## Configuration Options

- **connection_url** (required): PostgreSQL connection URL
- **read_only** (default: `true`): Only allow SELECT/SHOW/DESCRIBE/EXPLAIN/WITH statements
- **verify_ssl** (default: `true`): Verify SSL certificates
- **max_rows** (default: `200`): Maximum rows to return (1-10000)
- **llm_instructions**: Context about this database

## Common Use Cases

```
"Why is this query slow: SELECT * FROM orders WHERE customer_id = 12345"
```

```
"Show me the schema for the users table"
```

```
"List all tables and their sizes"
```

```
"Check for missing indexes on the orders table"
```
