<div align="center">
  <h1 align="center">HolmesGPT — The CNCF SRE Agent</h1>

  <p align="center">
    <a href="#installation"><strong>Installation</strong></a> |
    <a href="https://holmesgpt.dev/"><strong>Docs</strong></a> |
    <a href="https://deepwiki.com/HolmesGPT/holmesgpt"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki"></a>
  </p>
</div>

Open-source AI agent for investigating production incidents and finding root causes. A [CNCF Sandbox project](./ADOPTERS.md) by [Robusta.Dev](https://home.robusta.dev/).

- **Petabyte-scale data**: Server-side filtering, JSON tree traversal, and tool output transformers keep large payloads out of context windows
- **[Deep integrations](https://holmesgpt.dev/data-sources/builtin-toolsets/)**: Prometheus, Grafana, Datadog, Kubernetes, and [many more](#-data-sources)—plus any [REST API](https://holmesgpt.dev/data-sources/api-toolsets/)
- **Bidirectional alert integrations**: Fetch alerts from AlertManager, PagerDuty, OpsGenie, or Jira—and write findings back
- **[Any LLM provider](https://holmesgpt.dev/ai-providers/)**: OpenAI, Anthropic, Azure, Bedrock, Gemini, and more
- **[Operator mode](https://holmesgpt.dev/operator/)**: Run investigations on a schedule as a Kubernetes operator

## How it Works

HolmesGPT uses an **agentic loop** to query live observability data from multiple sources and identify root causes.

<img width="3114" alt="holmesgpt-architecture-diagram" src="https://github.com/user-attachments/assets/f659707e-1958-4add-9238-8565a5e3713a" />

![HolmesGPT Investigation Demo](https://holmesgpt.dev/assets/HolmesInvestigation.gif)

### 🔗 Data Sources

HolmesGPT integrates with popular observability and cloud platforms. The following data sources ("toolsets") are built-in. [Add your own](https://holmesgpt.dev/data-sources/custom-toolsets/).

| Data Source | Notes |
|-------------|-------|
| [<img src="images/integration_logos/aks-icon.png" alt="AKS" width="20" style="vertical-align: middle;"> **AKS**](https://holmesgpt.dev/data-sources/builtin-toolsets/aks/) | Azure Kubernetes Service cluster and node health diagnostics |
| [<img src="images/integration_logos/argocd-icon.png" alt="ArgoCD" width="20" style="vertical-align: middle;"> **ArgoCD**](https://holmesgpt.dev/data-sources/builtin-toolsets/argocd/) | Get status, history and manifests and more of apps, projects and clusters |
| [<img src="images/integration_logos/aws_logo.png" alt="AWS" width="20" style="vertical-align: middle;"> **AWS**](https://holmesgpt.dev/data-sources/builtin-toolsets/aws/) | RDS events, instances, slow query logs, and more (MCP) |
| [<img src="images/integration_logos/azure.png" alt="Azure" width="20" style="vertical-align: middle;"> **Azure**](https://holmesgpt.dev/data-sources/builtin-toolsets/azure-mcp/) | Azure resources and diagnostics (MCP) |
| [<img src="images/integration_logos/azure.png" alt="Azure SQL" width="20" style="vertical-align: middle;"> **Azure SQL**](https://holmesgpt.dev/data-sources/builtin-toolsets/azure-sql/) | Database health, performance, connections, and slow queries |
| [<img src="images/integration_logos/confluence_logo.png" alt="Confluence" width="20" style="vertical-align: middle;"> **Confluence**](https://holmesgpt.dev/data-sources/builtin-toolsets/confluence/) | Private runbooks and documentation |
| [<img src="images/integration_logos/coralogix-icon.png" alt="Coralogix" width="20" style="vertical-align: middle;"> **Coralogix**](https://holmesgpt.dev/data-sources/builtin-toolsets/coralogix-logs/) | Retrieve logs for any resource |
| [<img src="images/integration_logos/datadog_logo.png" alt="Datadog" width="20" style="vertical-align: middle;"> **Datadog**](https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/) | Query logs, metrics, and traces |
| [<img src="images/integration_logos/docker_logo.png" alt="Docker" width="20" style="vertical-align: middle;"> **Docker**](https://holmesgpt.dev/data-sources/builtin-toolsets/docker/) | Get images, logs, events, history and more |
| [<img src="images/integration_logos/opensearchserverless-icon.png" alt="Elasticsearch" width="20" style="vertical-align: middle;"> **Elasticsearch / OpenSearch**](https://holmesgpt.dev/data-sources/builtin-toolsets/elasticsearch/) | Query logs, cluster health, shard and index diagnostics |
| [<img src="images/integration_logos/gcpmonitoring-icon.png" alt="GCP" width="20" style="vertical-align: middle;"> **GCP**](https://holmesgpt.dev/data-sources/builtin-toolsets/gcp/) | Google Cloud Platform resources (MCP) |
| [<img src="images/integration_logos/github_logo.png" alt="GitHub" width="20" style="vertical-align: middle;"> **GitHub**](https://holmesgpt.dev/data-sources/builtin-toolsets/github-mcp/) | Repositories, issues, and pull requests (MCP) |
| [<img src="images/integration_logos/grafana-icon.png" alt="Grafana" width="20" style="vertical-align: middle;"> **Grafana**](https://holmesgpt.dev/data-sources/builtin-toolsets/grafanadashboards/) | Query and analyze dashboard configurations and panels |
| [<img src="images/integration_logos/helm_logo.png" alt="Helm" width="20" style="vertical-align: middle;"> **Helm**](https://holmesgpt.dev/data-sources/builtin-toolsets/helm/) | Release status, chart metadata, and values |
| [<img src="images/integration_logos/http-icon.png" alt="Internet" width="20" style="vertical-align: middle;"> **Internet**](https://holmesgpt.dev/data-sources/builtin-toolsets/internet/) | Public runbooks, community docs etc |
| [<img src="images/integration_logos/kafka_logo.png" alt="Kafka" width="20" style="vertical-align: middle;"> **Kafka**](https://holmesgpt.dev/data-sources/builtin-toolsets/kafka/) | Fetch metadata, list consumers and topics or find lagging consumer groups |
| [<img src="images/integration_logos/kubernetes-icon.png" alt="Kubernetes" width="20" style="vertical-align: middle;"> **Kubernetes**](https://holmesgpt.dev/data-sources/builtin-toolsets/kubernetes/) | Pod logs, K8s events, and resource status (kubectl describe) |
| [<img src="images/integration_logos/grafana_loki-icon.png" alt="Loki" width="20" style="vertical-align: middle;"> **Loki**](https://holmesgpt.dev/data-sources/builtin-toolsets/grafanaloki/) | Query logs for Kubernetes resources or any query |
| [<img src="images/integration_logos/postgres-icon.png" alt="MariaDB" width="20" style="vertical-align: middle;"> **MariaDB**](https://holmesgpt.dev/data-sources/builtin-toolsets/mariadb-mcp/) | MariaDB database queries and diagnostics (MCP) |
| [<img src="images/integration_logos/postgres-icon.png" alt="MongoDB Atlas" width="20" style="vertical-align: middle;"> **MongoDB Atlas**](https://holmesgpt.dev/data-sources/builtin-toolsets/mongodb-atlas/) | Cluster health, slow queries, and performance diagnostics |
| [<img src="images/integration_logos/newrelic_logo.png" alt="NewRelic" width="20" style="vertical-align: middle;"> **NewRelic**](https://holmesgpt.dev/data-sources/builtin-toolsets/newrelic/) | Investigate alerts, query tracing data |
| [<img src="images/integration_logos/openshift-icon.png" alt="OpenShift" width="20" style="vertical-align: middle;"> **OpenShift**](https://holmesgpt.dev/data-sources/builtin-toolsets/openshift/) | Projects, routes, builds, security context constraints, and deployment configs |
| [<img src="images/integration_logos/prometheus-icon.png" alt="Prometheus" width="20" style="vertical-align: middle;"> **Prometheus**](https://holmesgpt.dev/data-sources/builtin-toolsets/prometheus/) | Investigate alerts, query metrics and generate PromQL queries |
| [<img src="images/integration_logos/rabbit_mq_logo.png" alt="RabbitMQ" width="20" style="vertical-align: middle;"> **RabbitMQ**](https://holmesgpt.dev/data-sources/builtin-toolsets/rabbitmq/) | Partitions, memory/disk alerts, troubleshoot split-brain scenarios and more |
| [<img src="images/integration_logos/robusta_logo.png" alt="Robusta" width="20" style="vertical-align: middle;"> **Robusta**](https://holmesgpt.dev/data-sources/builtin-toolsets/robusta/) | Multi-cluster monitoring, historical change data, runbooks, PromQL graphs and more |
| [<img src="images/integration_logos/servicenow-icon.png" alt="ServiceNow" width="20" style="vertical-align: middle;"> **ServiceNow**](https://holmesgpt.dev/data-sources/builtin-toolsets/servicenow/) | Query tables and incident records |
| **Sentry** | Error tracking, issues, and performance monitoring (MCP) |
| [<img src="images/integration_logos/slab_logo.png" alt="Slab" width="20" style="vertical-align: middle;"> **Slab**](https://holmesgpt.dev/data-sources/builtin-toolsets/slab/) | Team knowledge base and runbooks on demand |
| **Splunk** | Log search and analysis (MCP) |
| [<img src="images/integration_logos/postgres-icon.png" alt="SQL Databases" width="20" style="vertical-align: middle;"> **SQL Databases**](https://holmesgpt.dev/data-sources/builtin-toolsets/database-postgresql/) | PostgreSQL, MySQL, ClickHouse, MariaDB, SQL Server, SQLite |
| [<img src="images/integration_logos/tempo_logo.png" alt="Tempo" width="20" style="vertical-align: middle;"> **Tempo**](https://holmesgpt.dev/data-sources/builtin-toolsets/grafanatempo/) | Fetch trace info, debug issues like high latency in application |

See the [full list of built-in toolsets](https://holmesgpt.dev/data-sources/builtin-toolsets/) for additional integrations including Cilium, KubeVela, Notion, Prefect, and more.

### 🚀 End-to-End Automation

HolmesGPT can fetch alerts/tickets to investigate from external systems, then write the analysis back to the source or Slack.

| Integration             | Status    | Notes |
|-------------------------|-----------|-------|
| Slack                   | ✅        | [Demo.](https://www.loom.com/share/afcd81444b1a4adfaa0bbe01c37a4847) Available via [Robusta.dev](https://home.robusta.dev/) (commercial platform) |
| Microsoft Teams         | ✅        | Available via [Robusta.dev](https://home.robusta.dev/) (commercial platform) |
| Prometheus/AlertManager | ✅        | Robusta SaaS or HolmesGPT CLI |
| PagerDuty               | ✅        | HolmesGPT CLI only |
| OpsGenie                | ✅        | HolmesGPT CLI only |
| Jira                    | ✅        | HolmesGPT CLI only |
| GitHub                  | ✅        | HolmesGPT CLI only |

## Installation

<a href="https://holmesgpt.dev/installation/cli-installation/">
  <img src="images/integration_logos/all-installation-methods.png" alt="All Installation Methods" style="max-width:100%; height:auto;">
</a>

Read the [installation documentation](https://holmesgpt.dev/installation/cli-installation/) to learn how to install HolmesGPT.

## Supported LLM Providers

<a href="https://holmesgpt.dev/ai-providers/">
  <img src="images/integration_logos/all-integration-providers.png" alt="All Integration Providers" style="max-width:100%; height:auto;">
</a>

Read the [LLM Providers documentation](https://holmesgpt.dev/ai-providers/) to learn how to set up your LLM API key.

## Using HolmesGPT

See the [walkthrough documentation](https://holmesgpt.dev/walkthrough/) for usage guides, including:

- [Interactive mode](https://holmesgpt.dev/walkthrough/interactive-mode/) for asking questions and follow-ups
- [Investigating Prometheus alerts](https://holmesgpt.dev/walkthrough/investigating-prometheus-alerts/)
- [CI/CD troubleshooting](https://holmesgpt.dev/walkthrough/cicd-troubleshooting/)

## 🔐 Data Privacy

By design, HolmesGPT has **read-only access** and respects RBAC permissions. It is safe to run in production environments.

## License
Distributed under the Apache 2.0 License. See [LICENSE](https://github.com/HolmesGPT/holmesgpt/blob/master/LICENSE) for more information.
<!-- Change License -->

## Community

Join our community to discuss the HolmesGPT roadmap and share feedback:

- [Community Meetups](https://docs.google.com/document/d/1q3L2iUd8tNu-NmZ6QIVOJcCLHrile9CC5QguOGTn_tg/edit?tab=t.0#heading=h.ihdnrt5bstrv)

## Support

If you have any questions, feel free to message us on [HolmesGPT Slack Channel](https://cloud-native.slack.com/archives/C0A1SPQM5PZ)

## How to Contribute

Please read our [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines and instructions.

For help, contact us on [Slack](https://cloud-native.slack.com/archives/C0A1SPQM5PZ) or ask [DeepWiki AI](https://deepwiki.com/HolmesGPT/holmesgpt) your questions.

Please make sure to follow the CNCF code of conduct - [details here](https://github.com/HolmesGPT/holmesgpt/blob/master/CODE_OF_CONDUCT.md).
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/HolmesGPT/holmesgpt)

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/11586/badge)](https://www.bestpractices.dev/projects/11586)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/HolmesGPT/holmesgpt/badge)](https://scorecard.dev/viewer/?uri=github.com/HolmesGPT/holmesgpt)
