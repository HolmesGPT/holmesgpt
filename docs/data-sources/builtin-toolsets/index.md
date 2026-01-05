# Built-in Toolsets

HolmesGPT includes pre-built integrations for popular monitoring and observability tools. Some work automatically with Kubernetes, while others require API keys or configuration.

## Available Toolsets

<div class="grid cards" markdown>

-   [:material-heart-pulse:{ .lg .middle } **AKS Node Health**](aks-node-health.md)
-   [:simple-argo:{ .lg .middle } **ArgoCD**](argocd.md)
-   [:material-aws:{ .lg .middle } **AWS**](aws.md)
-   [:material-microsoft-azure:{ .lg .middle } **Azure Kubernetes Service**](aks.md)
-   [:material-database:{ .lg .middle } **Azure SQL Database**](azure-sql.md)
-   [:material-console:{ .lg .middle } **Bash**](bash.md)
-   [:simple-cilium:{ .lg .middle } **Cilium**](cilium.md)
-   [:simple-confluence:{ .lg .middle } **Confluence**](confluence.md)
-   [:material-network:{ .lg .middle } **Connectivity Check**](connectivity-check.md)
-   [:material-chart-line:{ .lg .middle } **Coralogix**](coralogix-logs.md)
-   [:simple-datadog:{ .lg .middle } **Datadog**](datadog.md)
-   [:material-clock:{ .lg .middle } **Datetime**](datetime.md)
-   [:simple-docker:{ .lg .middle } **Docker**](docker.md)
-   [:material-google-cloud:{ .lg .middle } **GCP (MCP)**](gcp.md)
-   [:material-github:{ .lg .middle } **GitHub**](github.md)
-   [:simple-grafana:{ .lg .middle } **Grafana Dashboards**](grafanadashboards.md)
-   [:material-package:{ .lg .middle } **Helm**](helm.md)
-   [:material-web:{ .lg .middle } **Internet**](internet.md)
-   [:simple-apachekafka:{ .lg .middle } **Kafka**](kafka.md)
-   [:simple-kubernetes:{ .lg .middle } **Kubernetes**](kubernetes.md)
-   [:simple-grafana:{ .lg .middle } **Loki**](grafanaloki.md)
-   [:simple-mariadb:{ .lg .middle } **MariaDB (MCP)**](mariadb-mcp.md)
-   [:simple-mongodb:{ .lg .middle } **MongoDB Atlas**](mongodb-atlas.md)
-   [:simple-newrelic:{ .lg .middle } **New Relic**](newrelic.md)
-   [:simple-notion:{ .lg .middle } **Notion**](notion.md)
-   [:simple-opensearch:{ .lg .middle } **OpenSearch logs**](opensearch-logs.md)
-   [:simple-opensearch:{ .lg .middle } **OpenSearch status**](opensearch-status.md)
-   [:simple-redhatopenshift:{ .lg .middle } **OpenShift**](openshift.md)
-   [:simple-prometheus:{ .lg .middle } **Prometheus**](prometheus.md)
-   [:simple-rabbitmq:{ .lg .middle } **RabbitMQ**](rabbitmq.md)
-   [:material-robot:{ .lg .middle } **Robusta**](robusta.md)
-   [:material-ticket:{ .lg .middle } **ServiceNow**](servicenow.md)
-   [:material-forum:{ .lg .middle } **Slab**](slab.md)
-   [:simple-grafana:{ .lg .middle } **Tempo**](grafanatempo.md)

</div>

## Getting Started

1. **Choose toolsets** that match your infrastructure (Prometheus, Grafana, etc.)
2. **Configure authentication** - some need API keys, others work automatically
3. **Run a test investigation** to verify data access

💡 **Tip**: Start with [Kubernetes](kubernetes.md) and [Prometheus](prometheus.md) for basic cluster monitoring.
