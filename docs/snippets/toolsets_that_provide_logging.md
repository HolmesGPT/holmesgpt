??? info "Available Log Sources"

    Multiple logging toolsets can be enabled simultaneously. HolmesGPT will use the most appropriate source for each investigation.

    - **[Kubernetes logs](kubernetes.md)** - Direct pod log access (enabled by default)
    - **[Loki](grafanaloki.md)** - Centralized logs via Loki
    - **[Elasticsearch / OpenSearch](elasticsearch.md)** - Logs from Elasticsearch/OpenSearch
    - **[Coralogix](coralogix-logs.md)** - Logs via Coralogix platform
    - **[DataDog](datadog.md)** - Logs from DataDog
