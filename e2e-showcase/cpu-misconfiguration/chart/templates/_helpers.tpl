{{- define "cpu-misconfiguration.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "cpu-misconfiguration.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cpu-misconfiguration.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "cpu-misconfiguration.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "cpu-misconfiguration.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
adnr.io/site-id: {{ required "siteId is required (e.g. edge-site-01)" .Values.siteId | quote }}
{{- end }}
