# OpenSearch status

By enabling this toolset, HolmesGPT will be able to access cluster metadata information like health, shards, and settings. This allows HolmesGPT to better troubleshoot problems with one or more OpenSearch clusters.

## Configuration

The configuration for OpenSearch is passed through to the underlying [opensearch-py library](https://github.com/opensearch-project/opensearch-py). Consult this library's [user guide](https://github.com/opensearch-project/opensearch-py/blob/main/USER_GUIDE.md) or [reference documentation](https://opensearch-project.github.io/opensearch-py/api-ref/clients/opensearch_client.html) for configuring the connection to OpenSearch, including how to authenticate this toolset to an OpenSearch cluster.

```yaml-toolset-config
toolsets:
    opensearch/status:
        enabled: true
        config:
            opensearch_clusters:
                - hosts:
                    - host1.com
                    - host2.com
                  headers:
                    header1: "value1"
                  use_ssl: <boolean>
                  ssl_assert_hostname: <boolean>
                  verify_certs: <boolean>
                  ssl_show_warn: <boolean>
                  http_auth:
                    username: <basic auth username>
                    password: <basic auth password>
```

Here is an example of an insecure OpenSearch configuration for local development using a bearer token:

=== "Holmes CLI"

    First, set the environment variables:
    ```bash
    export OPENSEARCH_URL="<opensearch host URL>"
    export OPENSEARCH_BEARER_TOKEN="<secret bearer token>"
    ```

    Then add the following to **~/.holmes/config.yaml**:
    ```yaml
    toolsets:
        opensearch/status:
            enabled: true
            config:
                opensearch_clusters:
                    - hosts:
                        - host: "{{ env.OPENSEARCH_URL }}"
                          port: 9200
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    ```yaml
    holmes:
        additionalEnvVars:
            - name: OPENSEARCH_URL
              value: "<opensearch host URL>"
            - name: OPENSEARCH_BEARER_TOKEN
              value: "<secret bearer token>"
        toolsets:
            opensearch/status:
                enabled: true
                config:
                    opensearch_clusters:
                        - hosts:
                            - host: "{{ env.OPENSEARCH_URL }}"
                              port: 9200
    ```

## Capabilities

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| opensearch_cluster_health | Get cluster health information |
| opensearch_cluster_stats | Get cluster statistics |
| opensearch_node_info | Get information about cluster nodes |
| opensearch_index_stats | Get statistics for specific indices |
| opensearch_shard_allocation | Get shard allocation information |
