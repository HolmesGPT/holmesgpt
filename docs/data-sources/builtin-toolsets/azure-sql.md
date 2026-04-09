# Azure SQL Database

!!! tip "Consider Azure MCP instead"
    Most users should start with the [Azure MCP](azure-mcp.md) integration, which provides broad access to all Azure APIs including Azure SQL. This standalone toolset is only needed if you require the specialized Query Store analysis tools that aren't available through the general MCP server.

By enabling this toolset, HolmesGPT can analyze Azure SQL Database performance, health, and operational issues using Azure REST APIs and Query Store data.

!!! warning
    This toolset is in **Experimental** stage.

## Prerequisites

1. Azure SQL Database instance
2. Azure authentication (Service Principal or Azure AD Workload Identity)
3. Appropriate Azure and SQL permissions

## Configuration

=== "Holmes CLI"

    **Azure AD Workload Identity**

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      azure/sql:
        enabled: true
        config:
          database:
            subscription_id: "your-subscription-id"
            resource_group: "your-resource-group"
            server_name: "your-azure-sql-server-name"
            database_name: "your-azure-sql-database-name"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    **Service Principal**

    ```yaml
    toolsets:
      azure/sql:
        enabled: true
        config:
          tenant_id: "your-tenant-id"
          client_id: "your-client-id"
          client_secret: "your-client-secret"
          database:
            subscription_id: "your-subscription-id"
            resource_group: "your-resource-group"
            server_name: "your-azure-sql-server-name"
            database_name: "your-azure-sql-database-name"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    **Azure AD Workload Identity**

    ```yaml
    holmes:
      toolsets:
        azure/sql:
          enabled: true
          config:
            database:
              subscription_id: "your-subscription-id"
              resource_group: "your-resource-group"
              server_name: "your-azure-sql-server-name"
              database_name: "your-azure-sql-database-name"
    ```

    **Service Principal**

    ```yaml
    holmes:
      toolsets:
        azure/sql:
          enabled: true
          config:
            tenant_id: "your-tenant-id"
            client_id: "your-client-id"
            client_secret: "your-client-secret"
            database:
              subscription_id: "your-subscription-id"
              resource_group: "your-resource-group"
              server_name: "your-azure-sql-server-name"
              database_name: "your-azure-sql-database-name"
    ```

## Roles / Access controls

The service principal requires these roles:

### Azure

```
Azure Level (RBAC):
├── Monitoring Reader (subscription)
├── SQL DB Contributor (resource group)
```

### SQL

```
Database Level (SQL permissions):
├── CREATE USER [holmes-service-principal] FROM EXTERNAL PROVIDER
├── GRANT VIEW SERVER STATE TO [holmes-service-principal]
└── ALTER ROLE db_datareader ADD MEMBER [holmes-service-principal]
```

### Query Store

In addition, Query Store should be enabled on target databases

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| analyze_database_health_status | Analyze overall database health and status |
| analyze_database_performance | Analyze database performance metrics |
| analyze_database_connections | Analyze database connection patterns and issues |
| analyze_database_storage | Analyze database storage usage and growth |
| get_top_cpu_queries | Get queries with highest CPU usage |
| get_slow_queries | Get slowest performing queries |
| get_top_data_io_queries | Get queries with highest data I/O usage |
| get_top_log_io_queries | Get queries with highest log I/O usage |
| get_active_alerts | Get active alerts for the database |
| analyze_connection_failures | Analyze connection failure patterns |
