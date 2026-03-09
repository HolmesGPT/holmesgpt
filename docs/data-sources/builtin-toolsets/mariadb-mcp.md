# MariaDB (MCP)

The MariaDB MCP server provides read-only access to MariaDB databases for troubleshooting performance issues, analyzing slow queries, investigating deadlocks, and diagnosing application problems related to database operations and much more.

## Overview

The MariaDB MCP server is deployed as a separate pod in your cluster when using the Holmes or Robusta Helm charts. 

For CLI users, you'll need to deploy the MCP server manually and configure Holmes to connect to it. 

It operates in read-only mode by default to ensure safety while investigating production databases (recommended to use a database user with read-only permissions as well).

## Configuration

=== "Holmes CLI"

    For CLI usage, you need to deploy the MariaDB MCP server first, then configure Holmes to connect to it. Below is an example on how to deploy it in your cluster.

    **Step 1: Deploy the MariaDB MCP Server**

    Create a file named `mariadb-mcp-deployment.yaml`:

    ```yaml
    apiVersion: v1
    kind: Namespace
    metadata:
      name: holmes-mcp
    ---
    apiVersion: v1
    kind: Secret
    metadata:
      name: mariadb-mcp-secret
      namespace: holmes-mcp
    type: Opaque
    stringData:
      username: "holmes_readonly"  # Your MariaDB username
      password: "your_password"     # Your MariaDB password
    ---
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: mariadb-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: mariadb-mcp-server
      template:
        metadata:
          labels:
            app: mariadb-mcp-server
        spec:
          containers:
          - name: mcp-server
            image: me-west1-docker.pkg.dev/robusta-development/development/mariadb-http-mcp-minimal:1.0.3
            imagePullPolicy: IfNotPresent
            ports:
            - containerPort: 8000
              name: http
              protocol: TCP
            env:
            - name: DB_HOST
              value: "mariadb.database.svc.cluster.local"  # Change to your MariaDB host
            - name: DB_PORT
              value: "3306"
            - name: DB_USER
              valueFrom:
                secretKeyRef:
                  name: mariadb-mcp-secret
                  key: username
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: mariadb-mcp-secret
                  key: password
            - name: DB_NAME
              value: "production_db"  # Change to your database name
            - name: MCP_READ_ONLY
              value: "true"
            - name: MCP_MAX_POOL_SIZE
              value: "5"
            - name: DB_SSL
              value: "false"  # Set to "true" if using SSL
            resources:
              requests:
                memory: "256Mi"
                cpu: "250m"
              limits:
                memory: "512Mi"
                cpu: "500m"
            livenessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 30
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            readinessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 15
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: mariadb-mcp-server
      namespace: holmes-mcp
    spec:
      type: ClusterIP
      ports:
      - port: 8000
        targetPort: 8000
        protocol: TCP
        name: http
      selector:
        app: mariadb-mcp-server
    ```

    Deploy it to your cluster:

    ```bash
    kubectl apply -f mariadb-mcp-deployment.yaml
    ```

    **Step 2: Update Database Credentials**

    Update the secret with your actual MariaDB credentials:

    ```bash
    kubectl delete secret mariadb-mcp-secret -n holmes-mcp
    kubectl create secret generic mariadb-mcp-secret \
      --from-literal=username=your_db_user \
      --from-literal=password=your_db_password \
      -n holmes-mcp
    ```

    **Step 3: Configure Holmes CLI**

    Add the MCP server configuration to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      mariadb:
        description: "MariaDB database troubleshooting and monitoring MCP server"
        config:
          url: "http://mariadb-mcp-server.holmes-mcp.svc.cluster.local:8000/mcp"
          mode: streamable-http
          headers:
            Content-Type: "application/json"
        llm_instructions: |
          Use this MariaDB MCP server to troubleshoot database issues.

          Sometimes, application that are working with the db can have latency, or even halt.
          This often because of issues related to the db, like slow queries because of missing indexes or inefficient queries, load on the db, DB locks etc.
          Checking the DB for this issue can help with finding the root cause.
          When you do find an issue, provide as much information as possible about the issue you found.

          When investigating issues, always:
          1. Check current connections and running queries first
          2. Look for deadlocks or lock waits if transactions are failing
          3. Analyze slow query patterns for performance issues
          4. Check table structures and indexes for optimization opportunities
          5. Review error logs if available

          The server provides tools for:
          1. **Database Inspection**:
             - List databases and tables
             - View table schemas and indexes
             - Check table statistics and sizes

          2. **Query Analysis**:
             - Execute read-only SQL queries for investigation
             - Analyze slow queries from the slow query log
             - Check current running queries with SHOW PROCESSLIST

          3. **Performance Troubleshooting**:
             - Identify deadlocks: Use "SHOW ENGINE INNODB STATUS" to see recent deadlocks
             - Find blocking queries: Check information_schema.innodb_locks and innodb_lock_waits
             - Analyze slow queries: Query performance_schema tables
             - Check connection usage: Use SHOW STATUS LIKE 'Threads_connected'
    ```

    **Step 4: Port Forwarding (Optional for Local Testing)**

    If running Holmes CLI locally and need to access the MCP server:

    ```bash
    kubectl port-forward -n holmes-mcp svc/mariadb-mcp-server 8000:8000
    ```

    Then update the URL in config.yaml to:
    ```yaml
    config:
      url: "http://localhost:8000/mcp"
    ```

=== "Holmes Helm Chart"

    Add the following minimal configuration to your `values.yaml` file:

    ```yaml
    mcpAddons:
      mariadb:
        enabled: true

        # Database connection configuration
        config:
          host: "mariadb.database.svc.cluster.local"  # Your MariaDB host
          database: "production_db"                    # Database name
          username: "holmes_readonly"                  # Database username
          password: "secure_password"                  # Database password
    ```

    For additional configuration options (resources, network policy, node selectors, SSL, etc.), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml#L113).

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Add the following minimal configuration to your `generated_values.yaml`:

    ```yaml
    globalConfig:
      # Your existing Robusta configuration

    # Add the Holmes MCP addon configuration
    holmes:
      mcpAddons:
        mariadb:
          enabled: true

          # Database connection configuration
          config:
            host: "mariadb.database.svc.cluster.local"  # Your MariaDB host
            database: "production_db"                    # Database name
            username: "holmes_readonly"                  # Database username
            password: "secure_password"                  # Database password
    ```

    For additional configuration options (resources, network policy, node selectors, SSL, etc.), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml#L113).

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
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

## Example Usage

### Slow running queries

```
"Are there any slow running queries on MariaDB?"
```

### Database Lock

```
"Are there any DB locks on MariaDB? What's causing it?"
```

### Application Hangs

```
"The checkout service is hanging when processing orders"
```

### Database Performance Issues

```
"Database queries are taking longer than usual"
```
