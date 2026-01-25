??? warning "Important: Disable Default Logging Toolset"

    Only one logging toolset should be enabled at a time. If you enable this toolset, disable the default `kubernetes/logs` toolset.

    **Available Log Sources:**

    - **[Kubernetes logs](kubernetes.md)** - Direct pod log access (enabled by default)
    - **[Loki](grafanaloki.md)** - Centralized logs via Loki
    - **[Elasticsearch / OpenSearch](elasticsearch.md)** - Logs from Elasticsearch/OpenSearch
    - **[Coralogix](coralogix-logs.md)** - Logs via Coralogix platform
    - **[DataDog](datadog.md)** - Logs from DataDog

    💡 **Choose one**: Only enable one logging toolset at a time for best performance.
