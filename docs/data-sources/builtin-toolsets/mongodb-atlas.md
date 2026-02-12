# MongoDB Atlas

By enabling this toolset, HolmesGPT can access MongoDB Atlas projects and processes to analyze logs, alerts, events, slow queries, and various metrics to understand the state of MongoDB projects.

!!! warning
    This toolset is in **Experimental** stage.

## Prerequisites

1. MongoDB Atlas account
2. MongoDB Atlas API keys (Public and Private)
3. MongoDB Atlas project ID
4. Appropriate MongoDB Atlas permissions

## Configuration

=== "Holmes CLI"

    First, set the following environment variables:

    ```bash
    export MONGODB_ATLAS_PUBLIC_KEY="<your-public-api-key>"
    export MONGODB_ATLAS_PRIVATE_KEY="<your-private-api-key>"
    export MONGODB_ATLAS_PROJECT_ID="<your-project-id>"
    ```

    Then add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      MongoDBAtlas:
        enabled: true
        config:
          public_key: "<your-public-api-key>"
          private_key: "<your-private-api-key>"
          project_id: "<your-project-id>"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      additionalEnvVars:
        - name: MONGODB_ATLAS_PUBLIC_KEY
          value: "<your-public-api-key>"
        - name: MONGODB_ATLAS_PRIVATE_KEY
          value: "<your-private-api-key>"
        - name: MONGODB_ATLAS_PROJECT_ID
          value: "<your-project-id>"
      toolsets:
        MongoDBAtlas:
          enabled: true
          config:
            public_key: "<your-public-api-key>"
            private_key: "<your-private-api-key>"
            project_id: "<your-project-id>"
    ```

## Setting up MongoDB Atlas API Keys

1. **Log into MongoDB Atlas** and navigate to your organization
2. **Go to Access Manager** → **API Keys**
3. **Create a new API key**:
   - Set appropriate permissions for your use case
   - Copy the public key and private key
4. **Get your Project ID**:
   - Navigate to your project
   - Copy the Project ID from the project settings

## Required Permissions

The API key requires the following permissions:

- **Project Read Only** - To read project information
- **Project Data Access Admin** - To access database logs and metrics
- **Project Monitoring Admin** - To access monitoring data and alerts

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| atlas_return_project_alerts | Get alerts for the MongoDB Atlas project |
| atlas_return_project_processes | Get information about processes in the project |
| atlas_return_project_slow_queries | Get slow queries from the project (last 24 hours) |
| atlas_return_events_from_project | Get events from the project (last 24 hours) |
| atlas_return_logs_for_host_in_project | Get logs for a specific host in the project |
| atlas_return_event_type_from_project | Get events of a specific type from the project |

## Usage Guidelines

### Performance Analysis

When investigating performance issues:

1. **Start with alerts and events**: Use `atlas_return_project_alerts` and `atlas_return_events_from_project` first to identify known issues
2. **Check slow queries**: Use `atlas_return_project_slow_queries` to identify performance bottlenecks
3. **Review logs**: Use `atlas_return_logs_for_host_in_project` for detailed log analysis

### Time Range Limitations

- `atlas_return_project_slow_queries` returns data from the last 24 hours only
- `atlas_return_events_from_project` returns data from the last 24 hours only
- If you need data from a different time range, the toolset will inform you it's not currently supported

### Query Analysis

- When analyzing slow queries, the toolset will show the actual query text for every slow query
- For requests asking for a specific number of slow queries (e.g., "top 10 slow queries"), the toolset will not duplicate queries from different processes

## Troubleshooting

### Common Issues

1. **Authentication Failures**
   - Verify your API keys are correct
   - Check that the API key has appropriate permissions
   - Ensure the project ID is correct

2. **Permission Errors**
   - Verify the API key has the required permissions listed above
   - Check that the key is associated with the correct organization

3. **No Data Returned**
   - Verify the project ID is correct
   - Check that there are active processes in the project
   - Ensure the time range contains relevant data

### API Rate Limits

MongoDB Atlas API has rate limits. If you encounter rate limiting:

- Wait before making additional requests
- Consider the frequency of your queries
- Check MongoDB Atlas documentation for current rate limits

## References

- [MongoDB Atlas API Documentation](https://www.mongodb.com/docs/atlas/reference/api-resources-spec/v2/)
- [MongoDB Atlas API Authentication](https://www.mongodb.com/docs/atlas/configure-api-access/)
- [MongoDB Atlas Monitoring](https://www.mongodb.com/docs/atlas/monitoring-and-alerts/)
