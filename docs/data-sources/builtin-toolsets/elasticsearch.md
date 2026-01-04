# Elasticsearch / OpenSearch

By enabling these toolsets, HolmesGPT can query Elasticsearch and OpenSearch clusters to investigate issues, search logs, analyze cluster health, and more.

These toolsets work with both **Elasticsearch** (including Elastic Cloud) and **OpenSearch** since they share the same REST API.

## Two Toolsets

HolmesGPT provides two separate Elasticsearch toolsets with different permission requirements:

| Toolset | Description | Permissions Required |
|---------|-------------|---------------------|
| `elasticsearch/data` | Search logs, metrics, and documents | Index-level read access |
| `elasticsearch/cluster` | Troubleshoot cluster health issues | Cluster-level monitor access |

Enable only the toolset(s) you need. Most users who just want to search logs only need `elasticsearch/data`.

## Configuration

=== "Data Querying Only"

    For searching logs and documents (lower permissions):

    ```yaml
    toolsets:
      elasticsearch/data:
        enabled: true
        config:
          url: "https://your-cluster.es.cloud.io:443"
          api_key: "your-api-key"
          verify_ssl: true
    ```

=== "Cluster Troubleshooting Only"

    For diagnosing cluster issues (requires cluster-level access):

    ```yaml
    toolsets:
      elasticsearch/cluster:
        enabled: true
        config:
          url: "https://your-cluster.es.cloud.io:443"
          api_key: "your-api-key"
          verify_ssl: true
    ```

=== "Both Toolsets"

    Enable both for full functionality:

    ```yaml
    toolsets:
      elasticsearch/data:
        enabled: true
        config:
          url: "{{ env.ELASTICSEARCH_URL }}"
          api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
      elasticsearch/cluster:
        enabled: true
        config:
          url: "{{ env.ELASTICSEARCH_URL }}"
          api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
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
        elasticsearch/data:
          enabled: true
          config:
            url: "{{ env.ELASTICSEARCH_URL }}"
            api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
        elasticsearch/cluster:
          enabled: true
          config:
            url: "{{ env.ELASTICSEARCH_URL }}"
            api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

## Authentication

The toolsets support multiple authentication methods:

| Method | Config Fields | Description |
|--------|--------------|-------------|
| API Key | `api_key` | Recommended for Elastic Cloud |
| Basic Auth | `username`, `password` | Username and password |
| None | - | For clusters without authentication |

### Other Options

| Option | Default | Description |
|--------|---------|-------------|
| `verify_ssl` | `true` | Verify SSL certificates |
| `timeout` | `10` | Request timeout in seconds |

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

### elasticsearch/data

| Tool Name | Description |
|-----------|-------------|
| elasticsearch_search | Search documents using Elasticsearch Query DSL |
| elasticsearch_mappings | Get field mappings for an index |
| elasticsearch_list_indices | List indices matching a pattern |

### elasticsearch/cluster

| Tool Name | Description |
|-----------|-------------|
| elasticsearch_cat | Query _cat APIs (indices, shards, nodes, etc.) |
| elasticsearch_cluster_health | Get cluster health status |
| elasticsearch_allocation_explain | Explain shard allocation decisions |
| elasticsearch_nodes_stats | Get node-level statistics |
| elasticsearch_index_stats | Get statistics for an index |

## Example Queries

- "Search for ERROR logs in the application-logs index from the last hour"
- "What are the field mappings for the metrics index?"
- "List all indices starting with 'logs-'"
- "What is the cluster health status?"
- "Why are shards unassigned?"
- "Which nodes have high disk usage?"
- "Show me the shards for the logs-* indices"

## OpenSearch Compatibility

These toolsets are fully compatible with OpenSearch clusters. Simply point the `url` to your OpenSearch endpoint:

```yaml
toolsets:
  elasticsearch/data:
    enabled: true
    config:
      url: "https://your-opensearch-cluster:9200"
      username: "admin"
      password: "your-password"
      verify_ssl: true
```
