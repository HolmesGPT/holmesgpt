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
{{- $isSelfHosted := false -}}
{{- range .Values.additionalEnvVars -}}
{{- if eq .name "ROBUSTA_API_ENDPOINT" -}}
{{- if ne .value "https://stg.api.robusta.dev" -}}{{- $isSelfHosted = true -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- $isSelfHosted -}}
{{- end -}}

{{- define "holmes.isDefaultSentryDSN" -}}
{{- eq .Values.sentryDSN "https://51f9cd9bd2fdee16144db08fc423cd3b@o1120648.ingest.us.sentry.io/4508799804702720" -}}
{{- end -}}

{{- define "holmes.sentryDSN" -}}
{{- if and (eq (include "holmes.isSelfHosted" .) "true") (eq (include "holmes.isDefaultSentryDSN" .) "true") -}}""{{- else -}}{{- .Values.sentryDSN -}}{{- end -}}
{{- end -}}
