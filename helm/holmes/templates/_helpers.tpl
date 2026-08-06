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
here. Same lookup pattern as holmes.kubernetesRemediationMcp.authTokenChecksum;
during `helm template` (no cluster) lookup returns nothing and the checksum is
stable, exactly like that precedent.
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

{{/*
Return the user-supplied config map as YAML, if any.
Supports both standalone (config) and subchart (holmes.config) value paths.
Usage: {{ include "holmes.config" . }}
*/}}
{{- define "holmes.config" -}}
{{- if .Values.config -}}
{{- .Values.config | toYaml -}}
{{- else if and .Values.holmes .Values.holmes.config -}}
{{- .Values.holmes.config | toYaml -}}
{{- end -}}
{{- end -}}

{{/*
Return the configured default model name, if any.
Supports both standalone (config.model) and subchart (holmes.config.model)
value paths.
Usage: {{- with (include "holmes.config.model" .) }}
*/}}
{{- define "holmes.config.model" -}}
{{- if .Values.config.model -}}
{{- .Values.config.model -}}
{{- else if and .Values.holmes .Values.holmes.config.model -}}
{{- .Values.holmes.config.model -}}
{{- end -}}
{{- end -}}
