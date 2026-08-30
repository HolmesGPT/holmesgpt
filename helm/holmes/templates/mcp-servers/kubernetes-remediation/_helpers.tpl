{{/*
Name of the Secret holding the HTTP bearer token shared by the remediation
server (MCP_AUTH_TOKEN) and the Holmes pod (K8S_REMEDIATION_MCP_TOKEN).
*/}}
{{- define "holmes.kubernetesRemediationMcp.authSecretName" -}}
{{- if .Values.mcpAddons.kubernetesRemediation.auth.existingSecret -}}
{{- .Values.mcpAddons.kubernetesRemediation.auth.existingSecret -}}
{{- else -}}
{{- .Release.Name -}}-k8s-remediation-mcp-auth
{{- end -}}
{{- end -}}

{{/*
Resolve the HTTP bearer token shared by the remediation server and Holmes.
Precedence: existing in-cluster Secret > freshly generated. (There is no
plaintext value knob — the token only ever lives in a Secret, so it never lands
in values or a pod-template annotation.)
The generated value is memoized on .Values so every caller in a single render
(the Secret's data and both pods' checksum annotations) sees the SAME token —
otherwise the enabling upgrade would write a random token in the Secret while a
pod-checksum lookup still saw the not-yet-created Secret as empty, and the next
upgrade would roll that pod spuriously. lookup returns nothing under
`helm template`/ArgoCD, so set auth.existingSecret for clusterless rendering.
When auth.existingSecret is set but not yet present at render time, this emits
nothing; the pods still read the token via secretKeyRef at runtime.
*/}}
{{- define "holmes.kubernetesRemediationMcp.authToken" -}}
{{- $auth := .Values.mcpAddons.kubernetesRemediation.auth -}}
{{- $existing := lookup "v1" "Secret" .Release.Namespace (include "holmes.kubernetesRemediationMcp.authSecretName" .) -}}
{{- if and $existing $existing.data (hasKey $existing.data "token") -}}
{{- index $existing.data "token" | b64dec -}}
{{- else if not $auth.existingSecret -}}
{{- if not (hasKey .Values "_k8sRemediationAuthToken") -}}
{{- $_ := set .Values "_k8sRemediationAuthToken" (randAlphaNum 48) -}}
{{- end -}}
{{- index .Values "_k8sRemediationAuthToken" -}}
{{- end -}}
{{- end -}}

{{/*
Checksum of the auth token for pod-template annotations: rolls the pods when
the token rotates (env comes from a Secret via secretKeyRef, which does not
restart pods on its own). Same source as the Secret data, so a stable token
produces a stable checksum and no needless restarts.
*/}}
{{- define "holmes.kubernetesRemediationMcp.authTokenChecksum" -}}
{{- include "holmes.kubernetesRemediationMcp.authToken" . | sha256sum -}}
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

- `read_file_from_container` — read a single file from inside a container (config files, on-disk logs). Secret/token mounts and /proc, /sys, /dev are always refused.
- `run_preapproved_kubectl_exec_command` — run an allowlisted read-only binary inside a container (ps/top/df/ls/netstat/ss via exec; only command[0] must be allowlisted). Use `read_file_from_container` instead of `cat`.
- `run_preapproved_diagnostic_image` — launch a short-lived pod from a pre-approved troubleshooting image (nicolaka/netshoot, busybox, curlimages/curl) for network/DNS/HTTP probing. The pod is auto-deleted.
- `get_remediation_mcp_config` — inspect the live effective policy.

## run_kubectl_command always pauses for a human

`run_kubectl_command` is the catch-all for everything not pre-approved — all mutations, arbitrary exec, non-allowlisted images. It ALWAYS requires human approval, so expect a wait. Use it only when a pre-approved tool can't accomplish the task, and express the full intent in one clear command.

## What gets refused

Non-allowlisted images, non-pre-approved read commands, denied file paths (secret/token mounts), blocked flags (`--kubeconfig`/`--context`/`--token`/`--as`/...), shell metacharacters, and verbs outside the hard allowlist.

## Examples

- `read_file_from_container(namespace="prod", pod="api-xxx", path="/app/config.yaml")`
- `run_preapproved_kubectl_exec_command(pod="api-xxx", namespace="prod", command=["ps","aux"])`
- `run_preapproved_diagnostic_image(image="nicolaka/netshoot", namespace="prod", command=["dig","my-svc"])`
- `run_kubectl_command(args=["rollout","restart","deployment/api","-n","prod"])`
{{- end -}}
{{- end -}}
