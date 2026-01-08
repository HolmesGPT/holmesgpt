<div align="center">
  <h1 align="center">AI Agent for Cloud Troubleshooting and Alert Investigation</h1>

HolmesGPT is an AI agent for investigating problems in your cloud, finding the root cause, and suggesting remediations. It has dozens of built-in integrations for cloud providers, observability tools, and on-call systems.

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/11586/badge)](https://www.bestpractices.dev/projects/11586)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/HolmesGPT/holmesgpt/badge)](https://scorecard.dev/viewer/?uri=github.com/HolmesGPT/holmesgpt)

>🎉 **HolmesGPT is now a CNCF Sandbox Project!**  
HolmesGPT was originally created by [Robusta.Dev](https://home.robusta.dev/) and is a CNCF sandbox project.

Find more about HolmesGPT's maintainers and adopters [here](./ADOPTERS.md).

📚 **[Read the full documentation at holmesgpt.dev](https://holmesgpt.dev/)** for installation guides, tutorials, API reference, and more.

  <p align="center">
    <a href="#how-it-works"><strong>How it Works</strong></a> |
    <a href="#installation"><strong>Installation</strong></a> |
    <a href="#supported-llm-providers"><strong>LLM Providers</strong></a> |
    <a href="https://www.youtube.com/watch?v=TfQfx65LsDQ"><strong>YouTube Demo</strong></a> |
    <a href="https://deepwiki.com/HolmesGPT/holmesgpt"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki"></a>
  </p>
</div>

![HolmesGPT Investigation Demo](https://holmesgpt.dev/assets/HolmesInvestigation.gif)

## How it Works

HolmesGPT connects AI models with live observability data and organizational knowledge. It uses an **agentic loop** to analyze data from multiple sources and identify possible root causes.

<img width="3114" alt="holmesgpt-architecture-diagram" src="https://github.com/user-attachments/assets/f659707e-1958-4add-9238-8565a5e3713a" />

### 🔗 Data Sources

HolmesGPT integrates with popular observability and cloud platforms. The following data sources ("toolsets") are built-in. [Add your own](#customizing-holmesgpt).

| Data Source | Status | Notes |
|-------------|--------|-------|
| [<img src="images/integration_logos/argocd-icon.png" alt="ArgoCD" width="20" style="vertical-align: middle;"> **ArgoCD**](https://holmesgpt.dev/data-sources/builtin-toolsets/argocd/) | ✅ | Get status, history and manifests and more of apps, projects and clusters |
| [<img src="images/integration_logos/aws_rds_logo.png" alt="AWS RDS" width="20" style="vertical-align: middle;"> **AWS RDS**](https://holmesgpt.dev/data-sources/builtin-toolsets/aws/) | ✅ | Fetch events, instances, slow query logs and more |
| [<img src="images/integration_logos/confluence_logo.png" alt="Confluence" width="20" style="vertical-align: middle;"> **Confluence**](https://holmesgpt.dev/data-sources/builtin-toolsets/confluence/) | ✅ | Private runbooks and documentation |
| [<img src="images/integration_logos/coralogix-icon.png" alt="Coralogix Logs" width="20" style="vertical-align: middle;"> **Coralogix Logs**](https://holmesgpt.dev/data-sources/builtin-toolsets/coralogix-logs/) | ✅ | Retrieve logs for any resource |
| [<img src="images/integration_logos/date_time_icon.png" alt="Datetime" width="20" style="vertical-align: middle;"> **Datetime**](https://holmesgpt.dev/data-sources/builtin-toolsets/datetime/) | ✅ | Date and time-related operations |
| [<img src="images/integration_logos/docker_logo.png" alt="Docker" width="20" style="vertical-align: middle;"> **Docker**](https://holmesgpt.dev/data-sources/builtin-toolsets/docker/) | ✅ | Get images, logs, events, history and more |
| [<img src="images/integration_logos/github_logo.png" alt="GitHub" width="20" style="vertical-align: middle;"> **GitHub**](https://holmesgpt.dev/data-sources/builtin-toolsets/github/) | 🟡 Beta | Remediate alerts by opening pull requests with fixes |
| [<img src="images/integration_logos/datadog_logo.png" alt="DataDog" width="20" style="vertical-align: middle;"> **DataDog**](https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/) | 🟡 Beta | Fetches log data from datadog  |
| [<img src="images/integration_logos/grafana_loki-icon.png" alt="Loki" width="20" style="vertical-align: middle;"> **Loki**](https://holmesgpt.dev/data-sources/builtin-toolsets/grafanaloki/) | ✅ | Query logs for Kubernetes resources or any query |
| [<img src="images/integration_logos/tempo_logo.png" alt="Tempo" width="20" style="vertical-align: middle;"> **Tempo**](https://holmesgpt.dev/data-sources/builtin-toolsets/grafanatempo/) | ✅ | Fetch trace info, debug issues like high latency in application. |
| [<img src="images/integration_logos/helm_logo.png" alt="Helm" width="20" style="vertical-align: middle;"> **Helm**](https://holmesgpt.dev/data-sources/builtin-toolsets/helm/) | ✅ | Release status, chart metadata, and values |
| [<img src="images/integration_logos/http-icon.png" alt="Internet" width="20" style="vertical-align: middle;"> **Internet**](https://holmesgpt.dev/data-sources/builtin-toolsets/internet/) | ✅ | Public runbooks, community docs etc |
| [<img src="images/integration_logos/kafka_logo.png" alt="Kafka" width="20" style="vertical-align: middle;"> **Kafka**](https://holmesgpt.dev/data-sources/builtin-toolsets/kafka/) | ✅ | Fetch metadata, list consumers and topics or find lagging consumer groups |
| [<img src="images/integration_logos/kubernetes-icon.png" alt="Kubernetes" width="20" style="vertical-align: middle;"> **Kubernetes**](https://holmesgpt.dev/data-sources/builtin-toolsets/kubernetes/) | ✅ | Pod logs, K8s events, and resource status (kubectl describe) |
| [<img src="images/integration_logos/newrelic_logo.png" alt="NewRelic" width="20" style="vertical-align: middle;"> **NewRelic**](https://holmesgpt.dev/data-sources/builtin-toolsets/newrelic/) | 🟡 Beta | Investigate alerts, query tracing data |
| [<img src="images/integration_logos/opensearchserverless-icon.png" alt="OpenSearch" width="20" style="vertical-align: middle;"> **OpenSearch**](https://holmesgpt.dev/data-sources/builtin-toolsets/opensearch-status/) | ✅ | Query health, shard, and settings related info of one or more clusters|
| [<img src="images/integration_logos/prometheus-icon.png" alt="Prometheus" width="20" style="vertical-align: middle;"> **Prometheus**](https://holmesgpt.dev/data-sources/builtin-toolsets/prometheus/) | ✅ | Investigate alerts, query metrics and generate PromQL queries  |
| [<img src="images/integration_logos/rabbit_mq_logo.png" alt="RabbitMQ" width="20" style="vertical-align: middle;"> **RabbitMQ**](https://holmesgpt.dev/data-sources/builtin-toolsets/rabbitmq/) | ✅ | Info about partitions, memory/disk alerts to troubleshoot split-brain scenarios and more  |
| [<img src="images/integration_logos/robusta_logo.png" alt="Robusta" width="20" style="vertical-align: middle;"> **Robusta**](https://holmesgpt.dev/data-sources/builtin-toolsets/robusta/) | ✅ | Multi-cluster monitoring, historical change data, user-configured runbooks, PromQL graphs and more |
| [<img src="images/integration_logos/slab_logo.png" alt="Slab" width="20" style="vertical-align: middle;"> **Slab**](https://holmesgpt.dev/data-sources/builtin-toolsets/slab/) | ✅ | Team knowledge base and runbooks on demand |

### 🚀 End-to-End Automation

HolmesGPT can fetch alerts/tickets to investigate from external systems, then write the analysis back to the source or Slack.

| Integration             | Status    | Notes |
|-------------------------|-----------|-------|
| Slack                   | 🟡 Beta   | [Demo.](https://www.loom.com/share/afcd81444b1a4adfaa0bbe01c37a4847) Tag HolmesGPT bot in any Slack message |
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

- In the Robusta SaaS: Go to [platform.robusta.dev](https://platform.robusta.dev/signup/?utm_source=github&utm_medium=holmesgpt-readme&utm_content=ways_to_use_holmesgpt_section) and use Holmes from your browser
- With HolmesGPT CLI: [setup an LLM API key](https://holmesgpt.dev/ai-providers/) and ask Holmes a question 👇

```bash
holmes ask "what pods are unhealthy and why?"
```

You can also provide files as context:
```bash
holmes ask "summarize the key points in this document" -f ./mydocument.txt
```

You can also load the prompt from a file using the `--prompt-file` option:
```bash
holmes ask --prompt-file ~/long-prompt.txt

Enter interactive mode to ask follow-up questions:
```bash
holmes ask "what pods are unhealthy and why?" --interactive
# or
holmes ask "what pods are unhealthy and why?" -i
```

Also supported:

<details>
<summary>HolmesGPT CLI: investigate Prometheus alerts</summary>

Pull alerts from AlertManager and investigate them with HolmesGPT:

```bash
holmes investigate alertmanager --alertmanager-url http://localhost:9093
# if on Mac OS and using the Holmes Docker image👇
#  holmes investigate alertmanager --alertmanager-url http://docker.for.mac.localhost:9093
```

<b>To investigate alerts in your browser, sign up for a free trial of [Robusta SaaS](https://platform.robusta.dev/signup/?utm_source=github&utm_medium=holmesgpt-readme&utm_content=ways_to_use_holmesgpt_section). </b>


<b>Optional:</b> port-forward to AlertManager before running the command mentioned above (if running Prometheus inside Kubernetes)

```bash
kubectl port-forward alertmanager-robusta-kube-prometheus-st-alertmanager-0 9093:9093 &
```
</details>

<details>
<summary>HolmesGPT CLI: investigate PagerDuty and OpsGenie alerts</summary>

```bash
holmes investigate opsgenie --opsgenie-api-key <OPSGENIE_API_KEY>
holmes investigate pagerduty --pagerduty-api-key <PAGERDUTY_API_KEY>
# to write the analysis back to the incident as a comment
holmes investigate pagerduty --pagerduty-api-key <PAGERDUTY_API_KEY> --update
```

For more details, run `holmes investigate <source> --help`
</details>

## Customizing HolmesGPT

HolmesGPT can investigate many issues out of the box, with no customization or training. Optionally, you can extend Holmes to improve results:

**Custom Data Sources**: Add data sources (toolsets) to improve investigations
   - If using Robusta SaaS: See [here](https://holmesgpt.dev/data-sources/custom-toolsets/)
   - If using the CLI: Use `-t` flag with [custom toolset files](./examples/custom_toolset.yaml) or add to `~/.holmes/config.yaml`

**Custom Runbooks**: Give HolmesGPT instructions for known alerts:
   - If using Robusta SaaS: Use the Robusta UI to add runbooks
   - If using the CLI: Use `-r` flag with [custom runbook files](./examples/custom_runbooks.yaml) or add to `~/.holmes/config.yaml`

You can save common settings and API Keys in a config file to avoid passing them from the CLI each time:

<details>
<summary>Reading settings from a config file</summary>

You can save common settings and API keys in config file for re-use. Place the config file in <code>~/.holmes/config.yaml`</code> or pass it using the <code> --config</code>

You can view an example config file with all available settings [here](config.example.yaml).

### Tool Output Transformers

HolmesGPT supports **transformers** to process large tool outputs before sending them to your primary LLM. This feature helps manage context window limits while preserving essential information.

The most common transformer is `llm_summarize`, which uses a fast secondary model to summarize lengthy outputs from tools like `kubectl describe`, log queries, or metrics collection.

📖 **Learn more**: [Tool Output Transformers Documentation](docs/transformers.md)
</details>

## 🔐 Data Privacy

By design, HolmesGPT has **read-only access** and respects RBAC permissions. It is safe to run in production environments.

We do **not** train HolmesGPT on your data. Data sent to Robusta SaaS is private to your account.

For extra privacy, [bring an API key](https://holmesgpt.dev/ai-providers/) for your own AI model.


## Evals

Because HolmesGPT relies on LLMs, it relies on [a suite of pytest based evaluations](https://holmesgpt.dev/development/evals/) to ensure the prompt and HolmesGPT's default set of tools work as expected with LLMs.

- [Introduction to HolmesGPT's evals](https://holmesgpt.dev/development/evaluations/).
- [Write your own evals](https://holmesgpt.dev/development/evaluations/adding-evals/).
- [Use Braintrust to view analyze results (optional)](https://holmesgpt.dev/development/evaluations/reporting/).


## License
Distributed under the Apache 2.0 License. See [LICENSE](https://github.com/HolmesGPT/holmesgpt/blob/master/LICENSE) for more information.
<!-- Change License -->

## Community

Join our community to discuss the HolmesGPT roadmap and share feedback:

📹 **First Community Meetup Recording:** [Watch on YouTube](https://youtu.be/slQRc6nlFQU)
- **Topics:** Roadmap discussion, community feedback, and Q&A
- **Resources:** [📝 Meeting Notes](https://docs.google.com/document/d/1sIHCcTivyzrF5XNvos7ZT_UcxEOqgwfawsTbb9wMJe4/edit?tab=t.0) | [📋 Community Page](https://holmesgpt.dev/community/)

## Support

If you have any questions, feel free to message us on [HolmesGPT Slack Channel](https://cloud-native.slack.com/archives/C0A1SPQM5PZ)

## How to Contribute

Please read our [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines and instructions.

For help, contact us on [Slack](https://cloud-native.slack.com/archives/C0A1SPQM5PZ) or ask [DeepWiki AI](https://deepwiki.com/HolmesGPT/holmesgpt) your questions.

Please make sure to follow the CNCF code of conduct - [details here](https://github.com/HolmesGPT/holmesgpt/blob/master/CODE_OF_CONDUCT.md).
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/HolmesGPT/holmesgpt)
