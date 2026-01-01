# Elasticsearch / OpenSearch

By enabling this toolset, HolmesGPT can query Elasticsearch and OpenSearch clusters to investigate issues, search logs, analyze cluster health, and more.

This toolset works with both **Elasticsearch** (including Elastic Cloud) and **OpenSearch** since they share the same REST API.

## Use Cases

This toolset supports two main use cases:

**1. Querying data stored in Elasticsearch** - Search logs, metrics, or other documents stored in your indices:

- Search for errors in application logs
- Query time-series data
- Explore index mappings and structure

**2. Troubleshooting Elasticsearch/OpenSearch cluster health** - Diagnose issues with the cluster itself:

- Check cluster health status (green/yellow/red)
- Investigate unassigned shards
- Analyze node statistics and resource usage
- Understand shard allocation decisions

Both use cases are served by the same `elasticsearch/core` toolset.

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**, creating the file if it doesn't exist:

    ```yaml
    toolsets:
      elasticsearch/core:
        enabled: true
        config:
          url: "https://your-cluster.es.cloud.io:443"
          api_key: "your-api-key"  # Or use username/password below
          # username: "elastic"
          # password: "your-password"
          verify_ssl: true
    ```

    You can also use environment variables:

    ```yaml
    toolsets:
      elasticsearch/core:
        enabled: true
        config:
          url: "{{ env.ELASTICSEARCH_URL }}"
          api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
          verify_ssl: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      additionalEnvVars:
        - name: ELASTICSEARCH_URL
          value: "https://your-cluster.es.cloud.io:443"
        - name: ELASTICSEARCH_API_KEY
          valueFrom:
            secretKeyRef:
              name: elasticsearch-credentials
              key: api-key
      toolsets:
        elasticsearch/core:
          enabled: true
          config:
            url: "{{ env.ELASTICSEARCH_URL }}"
            api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
            verify_ssl: true
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Authentication

The toolset supports multiple authentication methods:

| Method | Config Fields | Description |
|--------|--------------|-------------|
| API Key | `api_key` | Recommended for Elastic Cloud |
| Basic Auth | `username`, `password` | Username and password |
| None | - | For clusters without authentication |

## Capabilities

--8<-- "snippets/toolset_capabilities_intro.md"

### Data Querying Tools

| Tool Name | Description |
|-----------|-------------|
| elasticsearch_search | Search documents using Elasticsearch Query DSL |
| elasticsearch_mappings | Get field mappings for an index |
| elasticsearch_cat | Query _cat APIs (indices, shards, etc.) with optional index filtering |

### Cluster Health Tools

| Tool Name | Description |
|-----------|-------------|
| elasticsearch_cluster_health | Get cluster health status |
| elasticsearch_allocation_explain | Explain shard allocation decisions |
| elasticsearch_nodes_stats | Get node-level statistics |
| elasticsearch_index_stats | Get statistics for an index |

## Example Queries

### Querying Data

- "Search for ERROR logs in the application-logs index from the last hour"
- "What are the field mappings for the metrics index?"
- "Show me the shards for the logs-* indices"

### Troubleshooting Cluster Health

- "What is the cluster health status?"
- "Why are shards unassigned?"
- "Which nodes have high disk usage?"

## OpenSearch Compatibility

This toolset is fully compatible with OpenSearch clusters. Simply point the `url` to your OpenSearch endpoint:

```yaml
toolsets:
  elasticsearch/core:
    enabled: true
    config:
      url: "https://your-opensearch-cluster:9200"
      username: "admin"
      password: "your-password"
      verify_ssl: true
```
