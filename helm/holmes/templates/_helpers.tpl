{{/*
Return the service account name to use
*/}}
{{- define "holmes.serviceAccountName" -}}
{{- if .Values.customServiceAccountName -}}
{{ .Values.customServiceAccountName }}
{{- else if .Values.createServiceAccount -}}
{{ .Release.Name }}-holmes-service-account
{{- else -}}
default
{{- end -}}
{{- end -}}

{{/*
API authentication: the default key shared by Holmes, the operator and the
Robusta runner (see apiAuth in values.yaml). "true" when the chart wires it.
The Robusta chart computes the same Secret name for the runner; keep the two
in sync.
*/}}
{{- define "holmes.apiAuth.enabled" -}}
{{- $apiAuth := .Values.apiAuth | default dict -}}
{{- if or $apiAuth.defaultKey $apiAuth.existingSecret -}}true{{- end -}}
{{- end -}}

{{- define "holmes.apiAuth.secretName" -}}
{{- $apiAuth := .Values.apiAuth | default dict -}}
{{- if $apiAuth.existingSecret -}}
{{- $apiAuth.existingSecret -}}
{{- else -}}
{{- printf "%s-holmes-api-key" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
The Secret holds the key ("token") and its SHA-256 ("token-sha256"). Each pod
mounts only the item it needs, as an optional file: a pod that starts before
the hook created the Secret still starts, and kubelet fills the file in once it
exists.

- The Holmes server only verifies keys, so it mounts only the hash: a tool
  that reads files in the Holmes container (the bash toolset) finds nothing it
  can authenticate with.
- Callers (the operator; the Robusta runner does the same) mount only the key,
  under /var/run/secrets/, which the remediation MCP's file reader refuses.
*/}}
{{- define "holmes.apiAuth.serverMountPath" -}}/etc/holmes/api-key{{- end -}}
{{- define "holmes.apiAuth.clientMountPath" -}}/var/run/secrets/holmes-api-key{{- end -}}

{{- define "holmes.apiAuth.serverVolume" -}}
- name: holmes-api-key
  secret:
    secretName: {{ include "holmes.apiAuth.secretName" . }}
    optional: true
    items:
      - key: token-sha256
        path: token-sha256
{{- end -}}

{{- define "holmes.apiAuth.serverVolumeMount" -}}
- name: holmes-api-key
  mountPath: {{ include "holmes.apiAuth.serverMountPath" . }}
  readOnly: true
{{- end -}}

{{- define "holmes.apiAuth.clientVolume" -}}
- name: holmes-api-key
  secret:
    secretName: {{ include "holmes.apiAuth.secretName" . }}
    optional: true
    items:
      - key: token
        path: token
{{- end -}}

{{- define "holmes.apiAuth.clientVolumeMount" -}}
- name: holmes-api-key
  mountPath: {{ include "holmes.apiAuth.clientMountPath" . }}
  readOnly: true
{{- end -}}

{{/*
Determine if this is a Robusta (hosted) environment.
Returns "true" if ROBUSTA_UI_DOMAIN is not set OR ends with "robusta.dev"
*/}}
{{- define "holmes.isSaasEnvironment" -}}
{{- $robustaUiDomain := "" -}}
{{- range .Values.additionalEnvVars -}}
  {{- if eq .name "ROBUSTA_UI_DOMAIN" -}}
    {{- $robustaUiDomain = .value -}}
  {{- end -}}
{{- end -}}
{{- if or (eq $robustaUiDomain "") (hasSuffix ".robusta.dev" $robustaUiDomain) -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
- If enableTelemetry field exists in values: use its value
- If field does not exist: true for SaaS environments, false otherwise
*/}}
{{- define "holmes.enableTelemetry" -}}
{{- if hasKey .Values "enableTelemetry" -}}
{{- .Values.enableTelemetry -}}
{{- else if eq (include "holmes.isSaasEnvironment" .) "true" -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
Common annotations to apply to all objects created by this chart.
Usage: {{- include "holmes.commonAnnotations" . | nindent 4 }}
*/}}
{{- define "holmes.commonAnnotations" -}}
{{- range $key, $val := .Values.commonAnnotations }}
{{ $key | toYaml }}: {{ $val | toString | toYaml }}
{{- end }}
{{- end }}

{{/*
Common labels to apply to all objects created by this chart.
Reserved keys used in selector.matchLabels are rejected to prevent
Deployment reconciliation failures caused by label divergence.
Usage: {{- include "holmes.commonLabels" . | nindent 4 }}
*/}}
{{- define "holmes.commonLabels" -}}
{{- $reserved := list
    "app"
    "app.kubernetes.io/name"
    "app.kubernetes.io/instance"
    "app.kubernetes.io/component"
    "app.kubernetes.io/part-of"
    "app.kubernetes.io/managed-by" -}}
{{- with .Values.commonLabels }}
{{- range $key, $val := . }}
{{- if has $key $reserved }}
{{- fail (printf "commonLabels: key %q is reserved and cannot be overridden" $key) }}
{{- end }}
{{ $key | toYaml }}: {{ $val | toString | toYaml }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Checksum of the Secret DATA behind every skillRepos credential, for the
pod-template annotation: env comes from these Secrets via secretKeyRef, which
does not restart pods when the data rotates, so the rollout must be triggered
here. During `helm template` (no cluster) lookup returns nothing and the
checksum is stable.
*/}}
{{- define "holmes.skillRepoSecretsChecksum" -}}
{{- $parts := list -}}
{{- range .Values.skillRepos -}}
{{- with .tokenSecret -}}
{{- $existing := lookup "v1" "Secret" $.Release.Namespace .name -}}
{{- if and $existing $existing.data (hasKey $existing.data .key) -}}
{{- $parts = append $parts (dict "name" .name "key" .key "value" (index $existing.data .key)) -}}
{{- end -}}
{{- end -}}
{{- with .githubApp -}}
{{- with .privateKeySecret -}}
{{- $existing := lookup "v1" "Secret" $.Release.Namespace .name -}}
{{- if and $existing $existing.data (hasKey $existing.data .key) -}}
{{- $parts = append $parts (dict "name" .name "key" .key "value" (index $existing.data .key)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /* Structured records, not joined values: concatenation would hash two
       adjacent secrets whose contents merely shifted a boundary identically. */ -}}
{{- toJson $parts | sha256sum -}}
{{- end -}}
