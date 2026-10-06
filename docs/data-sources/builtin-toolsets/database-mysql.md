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

```yaml-toolset-config
toolsets:
  orders-mysql:
    type: database
    config:
      connection_url: "{{ env.MYSQL_URL }}"
    llm_instructions: "Orders database with customer and product data"
---
secret:
  - --from-literal=MYSQL_URL='mysql+pymysql://holmes_readonly:your_secure_password@mysql.example.com:3306/orders'
cli: |
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
```

### Multiple instances

```yaml-toolset-config {secret-qualifier=instances}
toolsets:
  orders-mysql:
    type: database
    config:
      connection_url: "{{ env.ORDERS_MYSQL_URL }}"

  analytics-mysql:
    type: database
    config:
      connection_url: "{{ env.ANALYTICS_MYSQL_URL }}"
---
secret:
  - --from-literal=ORDERS_MYSQL_URL='mysql+pymysql://holmes_readonly:your_secure_password@mysql.example.com:3306/orders'
  - --from-literal=ANALYTICS_MYSQL_URL='mysql+pymysql://analyst:pass@analytics-mysql.internal:3306/analytics'
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
