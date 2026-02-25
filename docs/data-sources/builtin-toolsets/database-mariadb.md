# MariaDB

Connect HolmesGPT to MariaDB databases to analyze query performance, investigate slow queries, check replication status, examine database health, and read data for troubleshooting.

You can configure multiple MariaDB instances with different names (e.g., `app-mariadb`, `cache-mariadb`, `staging-mariadb`).

## Creating a Read-Only User

```sql
-- Create user
CREATE USER 'holmes_readonly'@'%' IDENTIFIED BY 'your_secure_password';

-- Grant read-only permissions
GRANT SELECT, SHOW VIEW, PROCESS, REPLICATION CLIENT ON *.* TO 'holmes_readonly'@'%';

-- Grant access to performance and information schemas
GRANT SELECT ON performance_schema.* TO 'holmes_readonly'@'%';
GRANT SELECT ON information_schema.* TO 'holmes_readonly'@'%';

FLUSH PRIVILEGES;
```

## Configuration

=== "Holmes CLI"

    **~/.holmes/config.yaml:**

    ```yaml
    toolsets:
      app-mariadb:
        type: database
        config:
          connection_url: "mysql+pymysql://holmes_readonly:your_secure_password@mariadb.example.com:3306/appdb"
        llm_instructions: "Application database with user and session data"

      cache-mariadb:
        type: database
        config:
          connection_url: "mysql+pymysql://cache_user:pass@cache-mariadb.internal:3306/cache"
        llm_instructions: "Cache database for session storage"
    ```

    **Using environment variables:**

    ```yaml
    toolsets:
      app-mariadb:
        type: database
        config:
          connection_url: "{{ env.MARIADB_URL }}"
    ```

    **Connection URL format:**
    ```
    mysql+pymysql://[username]:[password]@[host]:[port]/[database]
    ```

    Note: MariaDB uses MySQL wire protocol, so use `mysql+pymysql://` in the connection URL.

=== "Holmes Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic mariadb-credentials \
      --from-literal=url='mysql+pymysql://holmes_readonly:your_secure_password@mariadb.example.com:3306/appdb' \
      -n holmes
    ```

    **Step 2: Configure in values.yaml**

    ```yaml
    additionalEnvVars:
      - name: MARIADB_URL
        valueFrom:
          secretKeyRef:
            name: mariadb-credentials
            key: url

    toolsets:
      app-mariadb:
        type: database
        config:
          connection_url: "{{ env.MARIADB_URL }}"
        llm_instructions: "Application database with user and session data"
    ```

    **Multiple instances:**

    ```yaml
    additionalEnvVars:
      - name: APP_MARIADB_URL
        valueFrom:
          secretKeyRef:
            name: mariadb-app
            key: url
      - name: CACHE_MARIADB_URL
        valueFrom:
          secretKeyRef:
            name: mariadb-cache
            key: url

    toolsets:
      app-mariadb:
        type: database
        config:
          connection_url: "{{ env.APP_MARIADB_URL }}"

      cache-mariadb:
        type: database
        config:
          connection_url: "{{ env.CACHE_MARIADB_URL }}"
    ```

=== "Robusta Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic mariadb-credentials \
      --from-literal=url='mysql+pymysql://holmes_readonly:your_secure_password@mariadb.example.com:3306/appdb' \
      -n default
    ```

    **Step 2: Configure in values.yaml**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: MARIADB_URL
          valueFrom:
            secretKeyRef:
              name: mariadb-credentials
              key: url

      toolsets:
        app-mariadb:
          type: database
          config:
            connection_url: "{{ env.MARIADB_URL }}"
          llm_instructions: "Application database with user and session data"
    ```

    **Multiple instances:**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: APP_MARIADB_URL
          valueFrom:
            secretKeyRef:
              name: mariadb-app
              key: url
        - name: CACHE_MARIADB_URL
          valueFrom:
            secretKeyRef:
              name: mariadb-cache
              key: url

      toolsets:
        app-mariadb:
          type: database
          config:
            connection_url: "{{ env.APP_MARIADB_URL }}"

        cache-mariadb:
          type: database
          config:
            connection_url: "{{ env.CACHE_MARIADB_URL }}"
    ```

## Configuration Options

- **connection_url** (required): MariaDB connection URL
- **read_only** (default: `true`): Only allow SELECT/SHOW/DESCRIBE/EXPLAIN/WITH statements
- **verify_ssl** (default: `true`): Verify SSL certificates
- **max_rows** (default: `200`): Maximum rows to return (1-10000)
- **llm_instructions**: Context about this database

## Common Use Cases

```
"Analyze query performance: SELECT * FROM users WHERE last_login > NOW() - INTERVAL 30 DAY"
```

```
"Show replication status and lag"
```

```
"List tables by size"
```
