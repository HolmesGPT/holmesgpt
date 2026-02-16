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

=== "Holmes CLI"

    Add to your config file (`~/.holmes/config.yaml`):

    ```yaml
    toolsets:
      elasticsearch/data:
        enabled: true
        config:
          api_url: "https://your-cluster.es.cloud.io:443"
          api_key: "your-api-key"
          # Alternative: use basic auth instead of api_key
          # username: "elastic"
          # password: "your-password"
      elasticsearch/cluster:
        enabled: true
        config:
          api_url: "https://your-cluster.es.cloud.io:443"
          api_key: "your-api-key"
          # Alternative: use basic auth instead of api_key
          # username: "elastic"
          # password: "your-password"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your credentials:

    ```bash
    kubectl create secret generic elasticsearch-credentials \
      --from-literal=api-key=your-api-key
    ```

    Then add to your Holmes Helm values:

    ```yaml
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
          api_url: "{{ env.ELASTICSEARCH_URL }}"
          api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
          # Alternative: use basic auth instead of api_key
          # username: "{{ env.ELASTICSEARCH_USERNAME }}"
          # password: "{{ env.ELASTICSEARCH_PASSWORD }}"
      elasticsearch/cluster:
        enabled: true
        config:
          api_url: "{{ env.ELASTICSEARCH_URL }}"
          api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
          # Alternative: use basic auth instead of api_key
          # username: "{{ env.ELASTICSEARCH_USERNAME }}"
          # password: "{{ env.ELASTICSEARCH_PASSWORD }}"
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your credentials:

    ```bash
    kubectl create secret generic elasticsearch-credentials \
      --from-literal=api-key=your-api-key
    ```

    Then add to your Robusta Helm values:

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
            api_url: "{{ env.ELASTICSEARCH_URL }}"
            api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
            # Alternative: use basic auth instead of api_key
            # username: "{{ env.ELASTICSEARCH_USERNAME }}"
            # password: "{{ env.ELASTICSEARCH_PASSWORD }}"
        elasticsearch/cluster:
          enabled: true
          config:
            api_url: "{{ env.ELASTICSEARCH_URL }}"
            api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
            # Alternative: use basic auth instead of api_key
            # username: "{{ env.ELASTICSEARCH_USERNAME }}"
            # password: "{{ env.ELASTICSEARCH_PASSWORD }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

!!! tip "Enable only what you need"
    You can enable just `elasticsearch/data` or `elasticsearch/cluster` depending on your needs. Most users who just want to search logs only need `elasticsearch/data`.

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
| `timeout_seconds` | `10` | Request timeout in seconds |

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

| Toolset | Tool Name | Description |
|---------|-----------|-------------|
| `elasticsearch/data` | elasticsearch_search | Search documents using Elasticsearch Query DSL |
| `elasticsearch/data` | elasticsearch_mappings | Get field mappings for an index |
| `elasticsearch/data` | elasticsearch_list_indices | List indices matching a pattern |
| `elasticsearch/cluster` | elasticsearch_cat | Query _cat APIs (indices, shards, nodes, etc.) |
| `elasticsearch/cluster` | elasticsearch_cluster_health | Get cluster health status |
| `elasticsearch/cluster` | elasticsearch_allocation_explain | Explain shard allocation decisions |
| `elasticsearch/cluster` | elasticsearch_nodes_stats | Get node-level statistics |
| `elasticsearch/cluster` | elasticsearch_index_stats | Get statistics for an index |

## Example Queries

- "Search for ERROR logs in the application-logs index from the last hour"
- "What are the field mappings for the metrics index?"
- "List all indices starting with 'logs-'"
- "What is the cluster health status?"
- "Why are shards unassigned?"
- "Which nodes have high disk usage?"
- "Show me the shards for the logs-* indices"

## OpenSearch Compatibility

These toolsets are fully compatible with OpenSearch clusters. Simply point the `api_url` to your OpenSearch endpoint:

```yaml
toolsets:
  elasticsearch/data:
    enabled: true
    config:
      api_url: "https://your-opensearch-cluster:9200"
      username: "admin"
      password: "your-password"
      verify_ssl: true
```
