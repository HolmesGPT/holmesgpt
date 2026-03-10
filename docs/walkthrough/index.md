# Walkthrough

Get started with HolmesGPT by running your first investigation.

## Prerequisites

Before starting, ensure you have:

- ✅ **HolmesGPT CLI installed** - See [CLI Installation Guide](../installation/cli-installation.md)
- ✅ **AI provider API key configured** - See [AI Provider Setup](../ai-providers/index.md)

## Run Your First Investigation

```bash
holmes ask "tell me something surprising about my environment"
```

Holmes will automatically discover your connected [data sources](../data-sources/builtin-toolsets/index.md) — Kubernetes, Prometheus, Datadog, Elasticsearch, AWS, GCP, databases, and more — and report back on what it finds.

## What You Just Experienced

HolmesGPT automatically:

- ✅ **Gathered context** - Retrieved relevant data from your observability stack
- ✅ **Identified the root cause** - Pinpointed the underlying issue
- ✅ **Provided actionable solutions** - Specific steps to fix the problem
- ✅ **Saved investigation time** - No manual troubleshooting steps required

## Next Steps

- **[Recommended Setup](../data-sources/recommended-setup.md)** - Connect metrics, logs, and cloud providers to unlock deeper investigations
- **[Troubleshooting guide](../reference/troubleshooting.md)** - Common issues and solutions
- **[Join our Slack](https://cloud-native.slack.com/archives/C0A1SPQM5PZ){:target="_blank"}** - Get help from the community
- **[Request features on GitHub](https://github.com/HolmesGPT/holmesgpt/issues){:target="_blank"}** - Suggest improvements or report bugs
