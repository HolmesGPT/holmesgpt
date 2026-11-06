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

{{/*
"true" when the server runs as a stdio subprocess of Holmes instead of as its own
Deployment: with a mounted kubeconfig it holds credentials for other clusters, and a
subprocess has no port another pod could reach them through.
*/}}
{{- define "holmes.kubernetesMcp.inHolmesPod" -}}
{{- if and .Values.mcpAddons.kubernetes.enabled .Values.mcpAddons.kubernetes.config.kubeconfig.secretName -}}
true
{{- end -}}
{{- end -}}

{{/*
The Secret holding the server's config.toml, as YAML {name, key}, or empty. Either
serverConfig (a Secret the chart generates) or configSecret (an external one).
*/}}
{{- define "holmes.kubernetesMcp.configSecret" -}}
{{- $config := .Values.mcpAddons.kubernetes.config -}}
{{- $serverConfig := $config.serverConfig | default "" -}}
{{- $external := $config.configSecret.secretName | default "" -}}
{{- if and $serverConfig $external -}}
{{- fail "mcpAddons.kubernetes.config: set either `serverConfig` (chart-generated Secret) or `configSecret.secretName` (external Secret), not both." -}}
{{- else if $serverConfig -}}
{{- dict "name" (printf "%s-k8s-mcp-server-config" .Release.Name) "key" "config.toml" | toYaml -}}
{{- else if $external -}}
{{- dict "name" $external "key" ($config.configSecret.secretKey | default "config.toml") | toYaml -}}
{{- end -}}
{{- end -}}

{{/*
Server arguments shared by the Deployment and the in-pod subprocess, as a YAML list.
*/}}
{{- define "holmes.kubernetesMcp.args" -}}
{{- $config := .Values.mcpAddons.kubernetes.config -}}
{{- $args := list -}}
{{- if $config.readOnly }}{{ $args = append $args "--read-only" }}{{ end -}}
{{- if $config.disableDestructive }}{{ $args = append $args "--disable-destructive" }}{{ end -}}
{{- if $config.toolsets }}{{ $args = concat $args (list "--toolsets" ($config.toolsets | toString)) }}{{ end -}}
{{- if $config.logLevel }}{{ $args = concat $args (list "--log-level" ($config.logLevel | toString)) }}{{ end -}}
{{- if include "holmes.kubernetesMcp.configSecret" . }}{{ $args = concat $args (list "--config" "/etc/kubernetes-mcp/config.toml") }}{{ end -}}
{{- if include "holmes.kubernetesMcp.inHolmesPod" . }}{{ $args = concat $args (list "--kubeconfig" "/etc/kubernetes/kubeconfig" "--cluster-provider" "kubeconfig") }}{{ end -}}
{{- range $config.extraArgs }}{{ $args = append $args (. | toString) }}{{ end -}}
{{- toYaml $args -}}
{{- end -}}

{{/*
Volume mounts for whichever container runs the server, as a YAML list (possibly empty).
*/}}
{{- define "holmes.kubernetesMcp.volumeMounts" -}}
{{- $mounts := list -}}
{{- if include "holmes.kubernetesMcp.inHolmesPod" . -}}
{{- $mounts = append $mounts (dict "name" "k8s-mcp-bin" "mountPath" "/opt/k8s-mcp" "readOnly" true) -}}
{{- $mounts = append $mounts (dict "name" "k8s-mcp-kubeconfig" "mountPath" "/etc/kubernetes" "readOnly" true) -}}
{{- end -}}
{{- if include "holmes.kubernetesMcp.configSecret" . -}}
{{- $mounts = append $mounts (dict "name" "mcp-config" "mountPath" "/etc/kubernetes-mcp" "readOnly" true) -}}
{{- end -}}
{{- if .Values.mcpAddons.kubernetes.config.certificateAuthority | default "" | trim -}}
{{- $mounts = append $mounts (dict "name" "mcp-ca" "mountPath" "/etc/kubernetes-mcp-ca" "readOnly" true) -}}
{{- end -}}
{{- toYaml $mounts -}}
{{- end -}}

{{/*
Volumes backing holmes.kubernetesMcp.volumeMounts, as a YAML list (possibly empty).
*/}}
{{- define "holmes.kubernetesMcp.volumes" -}}
{{- $config := .Values.mcpAddons.kubernetes.config -}}
{{- $volumes := list -}}
{{- if include "holmes.kubernetesMcp.inHolmesPod" . -}}
{{- $volumes = append $volumes (dict "name" "k8s-mcp-bin" "emptyDir" dict) -}}
{{- $volumes = append $volumes (dict "name" "k8s-mcp-kubeconfig" "secret" (dict "secretName" $config.kubeconfig.secretName "defaultMode" 420 "items" (list (dict "key" ($config.kubeconfig.secretKey | default "kubeconfig") "path" "kubeconfig")))) -}}
{{- end -}}
{{- with include "holmes.kubernetesMcp.configSecret" . | fromYaml -}}
{{- $volumes = append $volumes (dict "name" "mcp-config" "secret" (dict "secretName" .name "defaultMode" 420 "items" (list (dict "key" .key "path" "config.toml")))) -}}
{{- end -}}
{{- if $config.certificateAuthority | default "" | trim -}}
{{- $volumes = append $volumes (dict "name" "mcp-ca" "configMap" (dict "name" (printf "%s-k8s-mcp-config" .Release.Name) "defaultMode" 420 "items" (list (dict "key" "ca.crt" "path" "ca.crt")))) -}}
{{- end -}}
{{- toYaml $volumes -}}
{{- end -}}
