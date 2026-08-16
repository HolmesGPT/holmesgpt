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
Whether API-key authentication is enabled ("true"/"false"). Defaults to true.
Uses hasKey rather than `default` because `false | default true` yields true.
*/}}
{{- define "holmes.authEnabled" -}}
{{- $auth := .Values.auth | default dict -}}
{{- if hasKey $auth "enabled" -}}
{{- $auth.enabled -}}
{{- else -}}
true
{{- end -}}
{{- end -}}

{{/*
Name of the Secret holding the Holmes API key (HOLMES_API_KEY).
Honors auth.existingApiKeySecret, else the chart-managed secret.
*/}}
{{- define "holmes.apiKeySecretName" -}}
{{- $auth := .Values.auth | default dict -}}
{{- $auth.existingApiKeySecret | default (printf "%s-holmes-api-key" .Release.Name) -}}
{{- end -}}

{{/*
Checksum of the stable API-key inputs, used as a pod annotation so every
consumer (holmes, operator, robusta-runner) rolls together when the key
configuration changes. The generated random key can't be hashed here (each
template invocation of randAlphaNum yields a new value); it only changes on
first install (pods are new anyway) or under `helm template` without an
explicit key — a mode where users must set auth.apiKey/existingApiKeySecret.
*/}}
{{- define "holmes.apiKeyChecksum" -}}
{{- $auth := .Values.auth | default dict -}}
{{- list (include "holmes.authEnabled" .) ($auth.apiKey | default "") ($auth.existingApiKeySecret | default "") | toYaml | sha256sum -}}
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
