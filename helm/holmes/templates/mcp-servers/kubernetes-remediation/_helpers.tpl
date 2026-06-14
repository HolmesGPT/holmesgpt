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

- `read_file_from_container` — read a single file from inside a container (config files, on-disk logs, /proc). Secret/token mounts are always refused.
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
