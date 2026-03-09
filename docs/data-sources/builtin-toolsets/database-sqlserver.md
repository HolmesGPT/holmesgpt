# SQL Server

Connect HolmesGPT to Microsoft SQL Server databases to analyze query execution plans, investigate performance issues, check index fragmentation, examine database health, and read data for troubleshooting.

You can configure multiple SQL Server instances with different names (e.g., `sqlserver-prod`, `sqlserver-analytics`, `sqlserver-staging`).

## Creating a Read-Only User

```sql
-- Create SQL Server login
CREATE LOGIN holmes_readonly WITH PASSWORD = 'Your_Secure_Password123!';

-- Connect to target database
USE your_database;

-- Create database user
CREATE USER holmes_readonly FOR LOGIN holmes_readonly;

-- Grant read-only access
ALTER ROLE db_datareader ADD MEMBER holmes_readonly;

-- Grant view server state for DMVs and performance monitoring
GRANT VIEW SERVER STATE TO holmes_readonly;
GRANT VIEW DATABASE STATE TO holmes_readonly;
GRANT VIEW DEFINITION TO holmes_readonly;
```

**For Azure SQL Database:**
```sql
-- Azure SQL creates user directly in database
CREATE USER holmes_readonly WITH PASSWORD = 'Your_Secure_Password123!';

ALTER ROLE db_datareader ADD MEMBER holmes_readonly;
GRANT VIEW DATABASE STATE TO holmes_readonly;
GRANT VIEW DEFINITION TO holmes_readonly;
```

## Configuration

=== "Holmes CLI"

    **~/.holmes/config.yaml:**

    ```yaml
    toolsets:
      sqlserver-prod:
        type: database
        config:
          connection_url: "mssql+pymssql://holmes_readonly:Your_Secure_Password123!@sqlserver.example.com:1433/mydb"
        llm_instructions: "Production SQL Server database with application data"

      sqlserver-analytics:
        type: database
        config:
          connection_url: "mssql+pymssql://analyst:pass@analytics-sql.internal:1433/analytics"
        llm_instructions: "Analytics SQL Server for reporting and BI"
    ```

    **Using environment variables:**

    ```yaml
    toolsets:
      sqlserver-prod:
        type: database
        config:
          connection_url: "{{ env.SQLSERVER_URL }}"
    ```

    **Connection URL format:**
    ```
    mssql+pymssql://[username]:[password]@[host]:[port]/[database]
    ```

    **With encryption:**
    ```yaml
    connection_url: "mssql+pymssql://user:pass@server:1433/db?encrypt=true"
    ```

=== "Holmes Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic sqlserver-credentials \
      --from-literal=url='mssql+pymssql://holmes_readonly:Your_Secure_Password123!@sqlserver.example.com:1433/mydb' \
      -n holmes
    ```

    **Step 2: Configure in values.yaml**

    ```yaml
    additionalEnvVars:
      - name: SQLSERVER_URL
        valueFrom:
          secretKeyRef:
            name: sqlserver-credentials
            key: url

    toolsets:
      sqlserver-prod:
        type: database
        config:
          connection_url: "{{ env.SQLSERVER_URL }}"
        llm_instructions: "Production SQL Server database with application data"
    ```

    **Multiple instances:**

    ```yaml
    additionalEnvVars:
      - name: PROD_SQLSERVER_URL
        valueFrom:
          secretKeyRef:
            name: sqlserver-prod
            key: url
      - name: ANALYTICS_SQLSERVER_URL
        valueFrom:
          secretKeyRef:
            name: sqlserver-analytics
            key: url

    toolsets:
      sqlserver-prod:
        type: database
        config:
          connection_url: "{{ env.PROD_SQLSERVER_URL }}"

      sqlserver-analytics:
        type: database
        config:
          connection_url: "{{ env.ANALYTICS_SQLSERVER_URL }}"
    ```

=== "Robusta Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic sqlserver-credentials \
      --from-literal=url='mssql+pymssql://holmes_readonly:Your_Secure_Password123!@sqlserver.example.com:1433/mydb' \
      -n default
    ```

    **Step 2: Configure in values.yaml**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: SQLSERVER_URL
          valueFrom:
            secretKeyRef:
              name: sqlserver-credentials
              key: url

      toolsets:
        sqlserver-prod:
          type: database
          config:
            connection_url: "{{ env.SQLSERVER_URL }}"
          llm_instructions: "Production SQL Server database with application data"
    ```

    **Multiple instances:**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: PROD_SQLSERVER_URL
          valueFrom:
            secretKeyRef:
              name: sqlserver-prod
              key: url
        - name: ANALYTICS_SQLSERVER_URL
          valueFrom:
            secretKeyRef:
              name: sqlserver-analytics
              key: url

      toolsets:
        sqlserver-prod:
          type: database
          config:
            connection_url: "{{ env.PROD_SQLSERVER_URL }}"

        sqlserver-analytics:
          type: database
          config:
            connection_url: "{{ env.ANALYTICS_SQLSERVER_URL }}"
    ```

## Configuration Options

- **connection_url** (required): SQL Server connection URL
- **read_only** (default: `true`): Only allow SELECT/SHOW/DESCRIBE/EXPLAIN/WITH statements
- **verify_ssl** (default: `true`): Verify SSL certificates
- **max_rows** (default: `200`): Maximum rows to return (1-10000)
- **llm_instructions**: Context about this database

## Common Use Cases

```
"Analyze execution plan for: SELECT * FROM Orders WHERE CustomerId = 123"
```

```
"Show database size and file growth settings"
```

```
"Check for missing indexes on frequently queried tables"
```
