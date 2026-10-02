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

## Investigation Workflow

1. **Check cluster context**: Use configuration tools to verify which cluster you're connected to
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
