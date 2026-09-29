# MySQL

Connect HolmesGPT to MySQL databases to analyze query performance, investigate slow queries, optimize indexes, examine database health, and read data for troubleshooting.

You can configure multiple MySQL instances with different names (e.g., `orders-rds`, `analytics-mysql`, `staging-mysql`).

## Creating a Read-Only User

```sql
-- Create user
CREATE USER 'holmes_readonly'@'%' IDENTIFIED BY 'your_secure_password';

-- Grant read-only permissions
GRANT SELECT, SHOW VIEW, PROCESS ON *.* TO 'holmes_readonly'@'%';

-- Grant access to performance schema
GRANT SELECT ON performance_schema.* TO 'holmes_readonly'@'%';
GRANT SELECT ON information_schema.* TO 'holmes_readonly'@'%';

FLUSH PRIVILEGES;
```

**For specific database only:**
```sql
CREATE USER 'holmes_readonly'@'%' IDENTIFIED BY 'your_secure_password';
GRANT SELECT, SHOW VIEW ON your_database.* TO 'holmes_readonly'@'%';
GRANT SELECT ON performance_schema.* TO 'holmes_readonly'@'%';
GRANT SELECT ON information_schema.* TO 'holmes_readonly'@'%';
GRANT PROCESS ON *.* TO 'holmes_readonly'@'%';
FLUSH PRIVILEGES;
```

## Configuration

**Connection URL format:**

```
mysql+pymysql://[username]:[password]@[host]:[port]/[database]
```

=== "Holmes CLI"

    **~/.holmes/config.yaml:**

    ```yaml
    toolsets:
      orders-mysql:
        type: database
        config:
          connection_url: "mysql+pymysql://holmes_readonly:your_secure_password@mysql.example.com:3306/orders"
        llm_instructions: "Orders database with customer and product data"

      analytics-mysql:
        type: database
        config:
          connection_url: "mysql+pymysql://analyst:pass@analytics-mysql.internal:3306/analytics"
        llm_instructions: "Analytics database for reporting queries"
    ```

    **Using environment variables:**

    ```yaml
    toolsets:
      orders-mysql:
        type: database
        config:
          connection_url: "{{ env.MYSQL_URL }}"
    ```

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-database-mysql \
      --from-literal=MYSQL_URL='mysql+pymysql://holmes_readonly:your_secure_password@mysql.example.com:3306/orders' \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-database-mysql

    toolsets:
      orders-mysql:
        type: database
        config:
          connection_url: "{{ env.MYSQL_URL }}"
        llm_instructions: "Orders database with customer and product data"
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-database-mysql \
      --from-literal=MYSQL_URL='mysql+pymysql://holmes_readonly:your_secure_password@mysql.example.com:3306/orders' \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-database-mysql

      toolsets:
        orders-mysql:
          type: database
          config:
            connection_url: "{{ env.MYSQL_URL }}"
          llm_instructions: "Orders database with customer and product data"
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

### Multiple instances

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-database-mysql-instances \
      --from-literal=ORDERS_MYSQL_URL='mysql+pymysql://holmes_readonly:your_secure_password@mysql.example.com:3306/orders' \
      --from-literal=ANALYTICS_MYSQL_URL='mysql+pymysql://analyst:pass@analytics-mysql.internal:3306/analytics' \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-database-mysql-instances

    toolsets:
      orders-mysql:
        type: database
        config:
          connection_url: "{{ env.ORDERS_MYSQL_URL }}"

      analytics-mysql:
        type: database
        config:
          connection_url: "{{ env.ANALYTICS_MYSQL_URL }}"
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-database-mysql-instances \
      --from-literal=ORDERS_MYSQL_URL='mysql+pymysql://holmes_readonly:your_secure_password@mysql.example.com:3306/orders' \
      --from-literal=ANALYTICS_MYSQL_URL='mysql+pymysql://analyst:pass@analytics-mysql.internal:3306/analytics' \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-database-mysql-instances

      toolsets:
        orders-mysql:
          type: database
          config:
            connection_url: "{{ env.ORDERS_MYSQL_URL }}"

        analytics-mysql:
          type: database
          config:
            connection_url: "{{ env.ANALYTICS_MYSQL_URL }}"
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Configuration Options

- **connection_url** (required): MySQL connection URL
- **read_only** (default: `true`): Only allow SELECT/SHOW/DESCRIBE/EXPLAIN/WITH statements
- **verify_ssl** (default: `true`): Verify SSL certificates
- **max_rows** (default: `200`): Maximum rows to return (1-10000)
- **llm_instructions**: Context about this database

## Common Use Cases

```
"Analyze slow query: SELECT * FROM orders WHERE created_at > '2024-01-01'"
```

```
"Show table structure for products and suggest indexes"
```

```
"What are the 10 largest tables?"
```

```
"Check for missing indexes on frequently queried columns"
```
