{{/*
Define the LLM instructions for Kubernetes Remediation MCP
*/}}
{{- define "holmes.kubernetesRemediationMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.kubernetesRemediation.llmInstructions -}}
{{ .Values.mcpAddons.kubernetesRemediation.llmInstructions }}
{{- else -}}
This MCP server provides the ability to execute kubectl commands for Kubernetes remediation.

IMPORTANT: This server can execute write operations (edit, patch, delete, scale, rollout, drain, etc.). Only use write operations when explicitly asked to remediate or fix an issue.

## When to Use This MCP Server

Use this MCP when you need to:
- Remediate Kubernetes issues (restart pods, scale deployments, cordon nodes, etc.)
- Gather additional cluster information beyond what the read-only Kubernetes toolset provides
- Execute kubectl commands that require write access

## Available Operations

The kubectl tool accepts arguments as a list. Examples:
- `["get", "pods", "-n", "production"]`
- `["scale", "deployment/my-app", "--replicas=3", "-n", "production"]`
- `["rollout", "restart", "deployment/my-app", "-n", "production"]`
- `["cordon", "node-1"]`
- `["drain", "node-1", "--ignore-daemonsets", "--delete-emptydir-data"]`

## Important Guidelines

- Always confirm the current state before making changes (get/describe first)
- Use namespace flags (-n) to target specific namespaces
- For destructive operations (delete, drain), verify the target carefully
- Check rollout status after making changes to deployments
- Use labels and selectors to target specific resources when possible
{{- end -}}
{{- end -}}
