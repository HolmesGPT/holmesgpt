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


{{- define "holmes.isSelfHosted" -}}
  {{- range .Values.additionalEnvVars -}}
    {{- if eq .name "ROBUSTA_API_ENDPOINT" -}}
      true
    {{- end -}}
  {{- end -}}
{{- end -}}
