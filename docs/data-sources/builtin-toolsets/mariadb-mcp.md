# MariaDB (MCP)

The MariaDB MCP (Model Context Protocol) server provides read-only access to MariaDB databases for troubleshooting performance issues, analyzing slow queries, investigating deadlocks, and diagnosing application problems related to database operations.

## Overview

The MariaDB MCP server is deployed as an add-on to the Holmes Helm chart, providing a dedicated service that Holmes can use to query and analyze MariaDB databases. It operates in read-only mode by default to ensure safety while investigating production databases.

## Configuration

=== "Robusta Helm Chart"

    Add the following to your `values.yaml` file:

    ```yaml
    mcpAddons:
      mariadb:
        enabled: true

        # Image configuration (optional - defaults shown)
        image: "mariadb-http-mcp-minimal:1.0.3"
        registry: "me-west1-docker.pkg.dev/robusta-development/development"

        # Database connection configuration
        config:
          host: "mariadb.database.svc.cluster.local"  # Your MariaDB host
          port: "3306"                                 # MariaDB port
          database: "production_db"                    # Database name
          username: "holmes_readonly"                  # Database username
          password: "secure_password"                  # Database password
          namespace: ""                                 # Leave empty for release namespace

          # MCP server settings
          readOnlyMode: true                           # Enforce read-only (recommended)
          maxPoolSize: "5"                             # Connection pool size
          useSSL: false                                # SSL for DB connection

        # Resource limits (optional - defaults shown)
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"

        # Network isolation (recommended)
        networkPolicy:
          enabled: true

        # Pod configuration (optional)
        annotations: {}
        nodeSelector: {}
        tolerations: []
        affinity: {}
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Holmes CLI"

    For CLI usage, configure the MCP server in **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      mariadb:
        description: "MariaDB database troubleshooting"
        config:
          url: "http://your-mariadb-mcp-server:8000/mcp"
          mode: streamable-http
          headers:
            Content-Type: "application/json"
        llm_instructions: |
          Use this server to troubleshoot database issues.
    ```

## Database User Setup

Create a read-only user for Holmes to use:

```sql
-- Create the user
CREATE USER 'holmes_readonly'@'%' IDENTIFIED BY 'secure_password';

-- Grant read-only permissions
GRANT SELECT, SHOW VIEW, PROCESS, REPLICATION CLIENT ON *.* TO 'holmes_readonly'@'%';

-- Grant access to performance schema
GRANT SELECT ON performance_schema.* TO 'holmes_readonly'@'%';

-- Grant access to information schema
GRANT SELECT ON information_schema.* TO 'holmes_readonly'@'%';

-- Apply the changes
FLUSH PRIVILEGES;
```

## Using External Secrets

For production environments, use Kubernetes secrets to manage credentials:

1. Create a secret with your database credentials:

```bash
kubectl create secret generic mariadb-mcp-secret \
  --from-literal=username=holmes_readonly \
  --from-literal=password=your_secure_password \
  -n your-namespace
```

2. Reference the existing secret in your values.yaml:

```yaml
mcpAddons:
  mariadb:
    enabled: true
    config:
      host: "mariadb.database.svc.cluster.local"
      database: "production_db"
      # Don't provide username/password here
      # The deployment will use the existing mariadb-mcp-secret
```

## Capabilities

The MariaDB MCP server enables Holmes to:

### Performance Analysis
- Identify slow queries and their patterns
- Analyze query execution plans
- Check for missing or inefficient indexes
- Monitor connection pool usage
- Review table statistics and sizes

### Deadlock Investigation
- Detect current deadlocks
- Identify blocking transactions
- Analyze lock wait chains
- Review transaction history

### Database Health
- Check current connections and processes
- Monitor resource usage
- Review error logs
- Analyze table fragmentation

### Query Optimization
- Find queries not using indexes
- Identify full table scans
- Review query cache effectiveness
- Analyze temporary table usage

## Common Investigation Scenarios

### Application Latency

When applications experience database-related latency:

```
"My application response time increased after 2 PM"
```

Holmes will:
- Check for long-running queries
- Analyze slow query patterns
- Look for lock contention
- Review connection pool saturation
- Check for missing indexes

### Application Hangs

When applications hang or become unresponsive:

```
"The checkout service is hanging when processing orders"
```

Holmes will:
- Check for deadlocks
- Identify blocking transactions
- Review metadata locks
- Analyze connection exhaustion
- Look for table locks

### Database Performance Issues

When overall database performance degrades:

```
"Database queries are taking longer than usual"
```

Holmes will:
- Analyze buffer pool efficiency
- Check query cache hit rates
- Review temporary table creation
- Identify inefficient queries
- Look for resource bottlenecks

## Performance Tuning Insights

The MCP server helps identify:

1. **Missing Indexes**
   - Queries performing full table scans
   - Slow queries that could benefit from indexes
   - Unused indexes consuming resources

2. **Lock Contention**
   - Frequent deadlocks
   - Long lock wait times
   - Transaction bottlenecks

3. **Resource Issues**
   - Connection pool exhaustion
   - Memory pressure
   - I/O bottlenecks

4. **Query Problems**
   - Inefficient JOIN operations
   - Suboptimal query patterns
   - N+1 query problems

## Security Considerations

### Network Isolation

The network policy ensures:
- Only Holmes pods can access the MCP server
- MCP server can only connect to MariaDB
- All other traffic is blocked

### Read-Only Mode

The server operates in read-only mode:
- Prevents accidental data modifications
- Ensures investigation safety
- Complies with production access policies

### Credential Management

Best practices:
- Use Kubernetes secrets for credentials
- Implement credential rotation
- Audit database access logs
- Use SSL for external databases

## Advanced Configuration

### Custom LLM Instructions

Override default investigation patterns:

```yaml
mcpAddons:
  mariadb:
    llmInstructions: |
      Focus on transaction deadlocks and lock waits.
      Always check for missing indexes first.
      Prioritize queries from the orders table.
```

### High-Load Environments

For high-traffic databases:

```yaml
mcpAddons:
  mariadb:
    config:
      maxPoolSize: "20"  # Increase connection pool
    resources:
      requests:
        memory: "512Mi"
        cpu: "500m"
      limits:
        memory: "1Gi"
        cpu: "1000m"
```

### Multi-Database Setup

To monitor multiple databases, deploy multiple MCP instances with different configurations, each pointing to a different database.

## Troubleshooting

### Common Issues

1. **Connection Refused**
   - Verify database host and port
   - Check network policies
   - Ensure database is accessible from the cluster

2. **Authentication Failed**
   - Verify username and password
   - Check user permissions
   - Ensure user can connect from the MCP pod's IP

3. **Performance Schema Not Available**
   - Enable performance schema in MariaDB configuration
   - Grant SELECT permission on performance_schema
   - Restart database if needed

### Verification

To verify the MCP server is working:

```bash
# Check if the pod is running
kubectl get pods -n <namespace> | grep mariadb-mcp

# Check logs
kubectl logs -n <namespace> <mariadb-mcp-pod-name>

# Test from Holmes
holmes ask "Show me the current database connections"
```

### MariaDB Configuration

Ensure these are enabled in your MariaDB configuration:

```ini
[mysqld]
# Enable performance schema
performance_schema = ON

# Enable slow query log
slow_query_log = 1
slow_query_log_file = /var/log/mysql/slow.log
long_query_time = 2

# InnoDB settings for better diagnostics
innodb_status_output = ON
innodb_status_output_locks = ON
```

## Example Values for Common Scenarios

### Internal MariaDB in Kubernetes

```yaml
mcpAddons:
  mariadb:
    enabled: true
    config:
      host: "mariadb.database.svc.cluster.local"
      port: "3306"
      database: "myapp"
      username: "holmes"
      password: "secretpass"
```

### External MariaDB with SSL

```yaml
mcpAddons:
  mariadb:
    enabled: true
    config:
      host: "mariadb.example.com"
      port: "3306"
      database: "production"
      useSSL: true
    # Use existing secret for credentials
```

### High-Availability Setup

```yaml
mcpAddons:
  mariadb:
    enabled: true
    config:
      host: "mariadb-primary.database.svc.cluster.local"
      maxPoolSize: "10"
    resources:
      limits:
        memory: "1Gi"
    affinity:
      podAffinity:
        requiredDuringSchedulingIgnoredDuringExecution:
        - topologyKey: kubernetes.io/hostname
```
