# Walkthrough

Get started with HolmesGPT by running your first investigation.

## Prerequisites

Before starting, ensure you have:

- ✅ **HolmesGPT CLI installed** - See [CLI Installation Guide](../installation/cli-installation.md)
- ✅ **AI provider API key configured** - See [AI Provider Setup](../ai-providers/index.md)
- ✅ **kubectl access to a Kubernetes cluster** - Any cluster will work

## Run Your First Investigation

Let's investigate a pod with HolmesGPT to see the value it provides:

1. **Create a test pod with an issue:**
   ```bash
   kubectl apply -f https://raw.githubusercontent.com/robusta-dev/kubernetes-demos/main/pending_pods/pending_pod_node_selector.yaml
   ```

2. **Ask Holmes to investigate:**

    === "OpenAI (Default)"
        ```bash
        holmes ask "describe the user-profile-import pod and explain any issues"
        ```

    === "Azure OpenAI"
        ```bash
        holmes ask "describe the user-profile-import pod and explain any issues" --model="azure/<your-model-name>"
        ```

    === "Anthropic Claude"
        ```bash
        holmes ask "describe the user-profile-import pod and explain any issues" --model="anthropic/<your-model-name>"
        ```

    === "Google Gemini"
        ```bash
        holmes ask "describe the user-profile-import pod and explain any issues" --model="gemini/<your-model-name>"
        ```

    === "AWS Bedrock"
        ```bash
        holmes ask "describe the user-profile-import pod and explain any issues" --model="bedrock/<your-model-name>"
        ```

    === "Ollama"
        ```bash
        holmes ask "describe the user-profile-import pod and explain any issues" --model="ollama_chat/<your-model-name>"
        ```

3. **See the value:**

    Holmes will analyze the pod, identify that it's stuck in "Pending" state due to an invalid node selector, and suggest specific remediation steps - all without you needing to manually run `kubectl describe`, check events, or dig through logs.

## What You Just Experienced

HolmesGPT automatically:

- ✅ **Gathered context** - Retrieved pod status, events, and related information
- ✅ **Identified the root cause** - Invalid node selector preventing scheduling
- ✅ **Provided actionable solutions** - Specific commands to fix the issue
- ✅ **Saved investigation time** - No manual troubleshooting steps required

## Clean Up

Remove the test pod:

```bash
kubectl delete pod user-profile-import
```

## Next Steps

- **[Add integrations](../data-sources/index.md)** - Connect monitoring tools like Prometheus, Grafana, and DataDog
- **[Troubleshooting guide](../reference/troubleshooting.md)** - Common issues and solutions
- **[Join our Slack](https://cloud-native.slack.com/archives/C0A1SPQM5PZ){:target="_blank"}** - Get help from the community
- **[Request features on GitHub](https://github.com/HolmesGPT/holmesgpt/issues){:target="_blank"}** - Suggest improvements or report bugs
