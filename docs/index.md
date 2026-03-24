# HolmesGPT

Open-source SRE agent for investigating production incidents across any infrastructure — Kubernetes, VMs, cloud services, databases, and more.

## New: Operator Mode — Find Problems 24/7 in the Background

Most AI agents are great at troubleshooting problems, but still need a human to notice something is wrong and trigger an investigation. [Operator mode](operator/index.md) fixes that — HolmesGPT runs in the background 24/7, spots problems before your customers notice, and messages you in Slack with the fix. Connect the [GitHub integration](data-sources/builtin-toolsets/github-mcp.md) and it can even open PRs to fix what it finds.

While the operator itself runs in Kubernetes, health checks can query any data source Holmes is connected to — VMs, cloud services, databases, SaaS platforms, and more.

- **[Deployment Verification](operator/deployment-verification.md)** - Deploy a health check alongside your app to verify the new version is healthy
- **[Scheduled Health Checks](operator/scheduled-health-checks.md)** - Continuously monitor services and catch regressions automatically

![HolmesGPT Investigation](assets/HolmesInvestigation.gif)

## Quick Start

<div class="grid cards" markdown>

-   :material-console:{ .lg .middle } **[Install CLI](installation/cli-installation.md)**

    ---

    Run HolmesGPT from your terminal

    [:octicons-arrow-right-24: Install](installation/cli-installation.md)

-   :material-web:{ .lg .middle } **[Install UI/TUI](installation/ui-installation.md)**

    ---

    Use through a web interface or K9s plugin

    [:octicons-arrow-right-24: Install](installation/ui-installation.md)

-   :material-chart-line:{ .lg .middle } **[View Benchmarks](development/evaluations/index.md)**

    ---

    Compare LLM performance across 150+ test scenarios

    [:octicons-arrow-right-24: Benchmarks](development/evaluations/index.md)

</div>

## Already Installed?

**[Connect your data sources](data-sources/recommended-setup.md)** to unlock deeper investigations with metrics, logs, and cloud provider access.

## Need Help?

- **[Join our Slack](https://cloud-native.slack.com/archives/C0A1SPQM5PZ){:target="_blank"}** - Get help from the community
- **[Request features on GitHub](https://github.com/HolmesGPT/holmesgpt/issues){:target="_blank"}** - Suggest improvements or report bugs

<br/>
<br/>
<br/>
We are a Cloud Native Computing Foundation sandbox project. 

<img src="https://www.cncf.io/wp-content/uploads/2022/07/cncf-color-bg.svg" alt="CNCF Logo" style="height:64px; margin-top: 0.5em;" />
