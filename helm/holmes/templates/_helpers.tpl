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
Determine if this is a Robusta SaaS environment.
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
