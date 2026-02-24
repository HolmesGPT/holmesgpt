{{/*
Define the LLM instructions for Kubernetes Remediation MCP
*/}}
{{- define "holmes.kubernetesRemediationMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.kubernetesRemediation.llmInstructions -}}
{{ .Values.mcpAddons.kubernetesRemediation.llmInstructions }}
{{- else -}}
This MCP server provides the ability to execute kubectl commands for Kubernetes remediation and write operations.

IMPORTANT: For Kubernetes **read operations** (get, describe, logs), always prefer the built-in Kubernetes toolset or other read-only tools — they are faster, more efficient, and don't require special authorization. Use this MCP server specifically for **write operations** (edit, patch, delete, scale, rollout, drain, etc.) when you need to remediate or fix an issue.

## When to Use This MCP Server

Use this MCP when you need to:
- Remediate Kubernetes issues (restart pods, scale deployments, cordon nodes, etc.)
- Execute kubectl write commands (edit, patch, delete, scale, rollout, drain, cordon, uncordon, taint, label, annotate)

Do NOT use this MCP for:
- Reading pod status, logs, or resource descriptions — use the built-in Kubernetes toolset instead
- General cluster exploration — use other read-only tools

## Available Operations

The kubectl tool accepts arguments as a list. Examples:
- `["scale", "deployment/my-app", "--replicas=3", "-n", "production"]`
- `["rollout", "restart", "deployment/my-app", "-n", "production"]`
- `["cordon", "node-1"]`
- `["drain", "node-1", "--ignore-daemonsets", "--delete-emptydir-data"]`
- `["patch", "deployment/my-app", "-n", "production", "-p", "{\"spec\":{\"replicas\":3}}"]`

## Important Guidelines

- Always confirm the current state before making changes (use the built-in Kubernetes toolset to get/describe first)
- Use namespace flags (-n) to target specific namespaces
- For destructive operations (delete, drain), verify the target carefully
- Check rollout status after making changes to deployments
- Use labels and selectors to target specific resources when possible
{{- end -}}
{{- end -}}
