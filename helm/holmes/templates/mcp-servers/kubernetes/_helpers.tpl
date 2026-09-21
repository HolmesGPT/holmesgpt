{{/*
Define the LLM instructions for Kubernetes MCP
*/}}
{{- define "holmes.kubernetesMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.kubernetes.llmInstructions -}}
{{ .Values.mcpAddons.kubernetes.llmInstructions }}
{{- else -}}
This MCP server provides direct access to Kubernetes clusters for advanced cluster operations and troubleshooting.

## When to Use This MCP Server

Use the Kubernetes MCP when investigating:
- Pod failures, crash loops, or scheduling issues
- Resource consumption and node capacity problems
- Deployment rollout issues or scaling problems
- Kubernetes events and cluster-level diagnostics
- Helm release status and management

## Which Cluster These Tools Reach

These tools reach the clusters in this server's kubeconfig — NOT necessarily the
cluster Holmes itself runs on. Do not assume the two are the same.

- `configuration_contexts_list` enumerates the reachable clusters. It is
  registered ONLY when the kubeconfig holds more than one context, so its
  absence means a single cluster is configured — not that context selection
  failed. When it is absent, every tool here targets that one cluster; say
  which cluster you queried only if you can identify it, and never guess a name.
- When it IS available, call it before other tools and pass an explicit
  `context` argument, rather than relying on the default.
- For the LOCAL cluster Holmes runs on, prefer the `bash` toolset.
- For other clusters in the Robusta FLEET, use the platform's `remote_*` tools
  with an `agent_name`. Those are a different mechanism from the kubeconfig
  contexts here; do not confuse a context name with an `agent_name`.

## Investigation Workflow

1. **Check cluster context**: Establish which cluster you are querying (see above)
2. **List namespaces**: Identify the relevant namespace for the investigation
3. **Check events**: Look at Kubernetes events for warnings and errors
4. **Inspect pods**: Get pod status, logs, and resource usage
5. **Examine resources**: Get detailed resource definitions to identify misconfigurations
6. **Check node health**: Review node status and resource consumption

## Important Guidelines

- Always specify the namespace when querying namespaced resources
- Check events first - they often reveal the root cause quickly
- Use pod logs to understand application-level failures
- Compare resource requests/limits with actual usage via top commands
- When investigating scheduling issues, check node capacity and taints
{{- end -}}
{{- end -}}
