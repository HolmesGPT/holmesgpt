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

For Azure SQL Database, see the [Azure SQL Database](#azure-sql-database) section below.

## Configuration

**Connection URL format:**

```
mssql+pytds://[username]:[password]@[host]:[port]/[database]
```

Plain `mssql://` URLs and legacy `mssql+pymssql://` URLs are automatically rewritten to use the `pytds` driver.

=== "Holmes CLI"

    **~/.holmes/config.yaml:**

    ```yaml
    toolsets:
      sqlserver-prod:
        type: database
        config:
          connection_url: "mssql+pytds://holmes_readonly:Your_Secure_Password123!@sqlserver.example.com:1433/mydb"
        llm_instructions: "Production SQL Server database with application data"

      sqlserver-analytics:
        type: database
        config:
          connection_url: "mssql+pytds://analyst:pass@analytics-sql.internal:1433/analytics"
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

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-database-sqlserver \
      --from-literal=SQLSERVER_URL='mssql+pytds://holmes_readonly:Your_Secure_Password123!@sqlserver.example.com:1433/mydb' \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-database-sqlserver

    toolsets:
      sqlserver-prod:
        type: database
        config:
          connection_url: "{{ env.SQLSERVER_URL }}"
        llm_instructions: "Production SQL Server database with application data"
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-database-sqlserver \
      --from-literal=SQLSERVER_URL='mssql+pytds://holmes_readonly:Your_Secure_Password123!@sqlserver.example.com:1433/mydb' \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-database-sqlserver

      toolsets:
        sqlserver-prod:
          type: database
          config:
            connection_url: "{{ env.SQLSERVER_URL }}"
          llm_instructions: "Production SQL Server database with application data"
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

### Multiple instances

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-database-sqlserver-instances \
      --from-literal=PROD_SQLSERVER_URL='mssql+pytds://holmes_readonly:Your_Secure_Password123!@sqlserver.example.com:1433/mydb' \
      --from-literal=ANALYTICS_SQLSERVER_URL='mssql+pytds://analyst:pass@analytics-sql.internal:1433/analytics' \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-database-sqlserver-instances

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

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-database-sqlserver-instances \
      --from-literal=PROD_SQLSERVER_URL='mssql+pytds://holmes_readonly:Your_Secure_Password123!@sqlserver.example.com:1433/mydb' \
      --from-literal=ANALYTICS_SQLSERVER_URL='mssql+pytds://analyst:pass@analytics-sql.internal:1433/analytics' \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-database-sqlserver-instances

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

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## TLS encryption

Encryption is controlled by the `verify_ssl` option (default: `true`). When `true`, connections use TLS with certificate verification — this is what Azure SQL and other TLS-enforcing servers need. Set it to `false` for servers with self-signed certificates, which disables TLS entirely.

### Servers with an internal or private CA

If your SQL Server's certificate is issued by a private CA, keep `verify_ssl: true` and add the CA to Holmes's trust store. This keeps connections encrypted *and* verified, and applies to every Holmes integration, not just this toolset. Servers that require encryption cannot be reached with `verify_ssl: false`, so this is the correct option for a private-CA deployment.

Encode the CA certificate:

```bash
base64 -w0 internal-ca.pem
```

=== "Holmes CLI"

    Set the environment variable:

    ```bash
    export CERTIFICATE="<base64-encoded CA certificate>"
    ```

=== "Holmes Helm Chart"

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    certificate: "<base64-encoded CA certificate>"
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      certificate: "<base64-encoded CA certificate>"
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Azure SQL Database

Azure SQL Database works with this toolset over SQL authentication. Create a contained database user (Azure SQL does not use server-level logins for this):

```sql
-- Run in the target database
CREATE USER holmes_readonly WITH PASSWORD = 'Your_Secure_Password123!';

ALTER ROLE db_datareader ADD MEMBER holmes_readonly;
GRANT VIEW DATABASE STATE TO holmes_readonly;
GRANT VIEW DEFINITION TO holmes_readonly;
```

Then configure the connection:

```yaml
toolsets:
  azure-sql-prod:
    type: database
    config:
      connection_url: "mssql+pytds://holmes_readonly:Your_Secure_Password123!@yourserver.database.windows.net:1433/mydb"
    llm_instructions: "Production Azure SQL database with application data"
```

## Configuration Options

- **connection_url** (required): SQL Server connection URL
- **read_only** (default: `true`): Only allow SELECT/SHOW/DESCRIBE/EXPLAIN/WITH statements
- **verify_ssl** (default: `true`): Connect with TLS and certificate verification (required by Azure SQL). Set to `false` to disable TLS for servers with self-signed certificates
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
