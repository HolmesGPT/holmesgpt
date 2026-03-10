# Walkthrough

Get started with HolmesGPT by running your first investigation.

## Prerequisites

Before starting, ensure you have:

- ✅ **HolmesGPT CLI installed** - See [CLI Installation Guide](../installation/cli-installation.md)
- ✅ **AI provider API key configured** - See [AI Provider Setup](../ai-providers/index.md)

## Run Your First Investigation

Choose a quickstart based on your environment:

=== "Any Environment (No K8s Needed)"

    HolmesGPT works without Kubernetes. Ask it about any system you have access to:

    ```bash
    # Investigate a Prometheus alert
    holmes ask "what Prometheus alerts are currently firing and why?"

    # Ask about any infrastructure topic
    holmes ask "what is the health of my Elasticsearch cluster?"

    # Or ask a general question — Holmes uses whichever toolsets are configured
    holmes ask "are there any issues with my production databases?"
    ```

    Holmes will use whichever [data sources](../data-sources/builtin-toolsets/index.md) you have configured — Prometheus, Datadog, Elasticsearch, AWS, GCP, databases, and more.

=== "Kubernetes"

    If you have a Kubernetes cluster, try this guided example:

    1. **Create a test pod with an issue:**
        ```bash
        kubectl apply -f https://raw.githubusercontent.com/robusta-dev/kubernetes-demos/main/pending_pods/pending_pod_node_selector.yaml
        ```

    2. **Ask Holmes to investigate:**
        ```bash
        holmes ask "describe the user-profile-import pod and explain any issues"
        ```

    3. **Clean up:**
        ```bash
        kubectl delete pod user-profile-import
        ```

    Holmes will identify that the pod is stuck in "Pending" state due to an invalid node selector and suggest specific remediation steps.

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
