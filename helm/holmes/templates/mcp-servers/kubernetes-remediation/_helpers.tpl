{{/*
Name of the Secret holding the HTTP bearer token shared by the remediation
server (MCP_AUTH_TOKEN) and the Holmes pod (K8S_REMEDIATION_MCP_TOKEN).
The chart-managed Secret is created at deploy time by the auth-bootstrap hook
Job, never rendered, so `helm template`/ArgoCD output is deterministic.
*/}}
{{- define "holmes.kubernetesRemediationMcp.authSecretName" -}}
{{- if .Values.mcpAddons.kubernetesRemediation.auth.existingSecret -}}
{{- .Values.mcpAddons.kubernetesRemediation.auth.existingSecret -}}
{{- else -}}
{{- .Release.Name -}}-k8s-remediation-mcp-token
{{- end -}}
{{- end -}}

{{/*
auth.rotation, as annotations for the Holmes and server Deployments
(deploymentAnnotations also carries commonAnnotations). With the
generated token it goes on the Deployment itself: the change makes Helm/ArgoCD
run the auth-bootstrap hook, which replaces the token and restarts both pods,
so it must not roll them a second time. With existingSecret there is no hook,
so it goes on the pod template (next to the Secret's checksum) to roll them.
*/}}
{{- define "holmes.kubernetesRemediationMcp.authRotation" -}}
{{- .Values.mcpAddons.kubernetesRemediation.auth.rotation | default 0 | int64 | toString -}}
{{- end -}}

{{- define "holmes.kubernetesRemediationMcp.deploymentAnnotations" -}}
{{- include "holmes.commonAnnotations" . }}
{{- $k8s := .Values.mcpAddons.kubernetesRemediation }}
{{- if and $k8s.enabled $k8s.auth.enabled (not $k8s.auth.existingSecret) }}
robusta.dev/k8s-remediation-token-rotation: {{ include "holmes.kubernetesRemediationMcp.authRotation" . | quote }}
{{- end }}
{{- end -}}

{{/*
Checksum of auth.existingSecret's token, so a plain `helm upgrade` after the
user changes it rolls both pods. lookup is empty under `helm template`/ArgoCD,
where the value is a constant; bump auth.rotation there instead.
*/}}
{{- define "holmes.kubernetesRemediationMcp.existingSecretChecksum" -}}
{{- $secret := lookup "v1" "Secret" .Release.Namespace .Values.mcpAddons.kubernetesRemediation.auth.existingSecret | default dict -}}
{{- dig "data" "token" "" $secret | sha256sum -}}
{{- end -}}

{{- define "holmes.kubernetesRemediationMcp.podAuthAnnotations" -}}
{{- $k8s := .Values.mcpAddons.kubernetesRemediation -}}
{{- if and $k8s.enabled $k8s.auth.enabled $k8s.auth.existingSecret -}}
robusta.dev/k8s-remediation-token-rotation: {{ include "holmes.kubernetesRemediationMcp.authRotation" . | quote }}
checksum/k8s-remediation-auth-token: {{ include "holmes.kubernetesRemediationMcp.existingSecretChecksum" . }}
{{- end -}}
{{- end -}}

{{/*
Define the LLM instructions for Kubernetes Remediation MCP
*/}}
{{- define "holmes.kubernetesRemediationMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.kubernetesRemediation.llmInstructions -}}
{{ .Values.mcpAddons.kubernetesRemediation.llmInstructions }}
{{- else -}}
This MCP server lets you diagnose AND act on the cluster beyond what your own pod's RBAC allows: read files inside containers, run diagnostic pods, and remediate (mutate) the cluster.

Use this server ONLY for things the built-in tools can't do. NEVER use it for `get`/`describe`/`logs` — the built-in Kubernetes tools are faster and need no approval.

## Prefer the no-approval tools (reach for these first)

These run immediately, no human needed:

- `read_file_from_container` — read a single file from inside a container (config files, on-disk logs under the allowed roots). Secret/token mounts, credential files (.aws, .kube, .env, *.pem, *.key, ...) and /proc, /sys, /dev are always refused, and a path the container cannot resolve with `readlink` is refused — use `run_kubectl_command` for those.
- `run_preapproved_kubectl_command` — run a read-only diagnostic command (ps/top/df/ls/netstat/ss via exec). Use `read_file_from_container` instead of `cat`.
- `run_preapproved_diagnostic_image` — launch a short-lived pod from a pre-approved troubleshooting image (nicolaka/netshoot, busybox, curlimages/curl) for network/DNS/HTTP probing. The pod is auto-deleted.
- `get_remediation_mcp_config` — inspect the live effective policy.

## run_kubectl_command always pauses for a human

`run_kubectl_command` is the catch-all for everything not pre-approved — all mutations, arbitrary exec, non-allowlisted images. It ALWAYS requires human approval, so expect a wait. Use it only when a pre-approved tool can't accomplish the task, and express the full intent in one clear command.

## What gets refused

Non-allowlisted images, non-pre-approved read commands, denied file paths (secret/token mounts), blocked flags (`--kubeconfig`/`--context`/`--token`/`--as`/...), shell metacharacters, and verbs outside the hard allowlist.

## Examples

- `read_file_from_container(namespace="prod", pod="api-xxx", path="/app/config.yaml")`
- `run_preapproved_kubectl_command(args=["exec","api-xxx","-n","prod","--","ps","aux"])`
- `run_preapproved_diagnostic_image(image="nicolaka/netshoot", namespace="prod", command=["dig","my-svc"])`
- `run_kubectl_command(args=["rollout","restart","deployment/api","-n","prod"])`
{{- end -}}
{{- end -}}
