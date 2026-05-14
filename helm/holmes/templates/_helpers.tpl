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
