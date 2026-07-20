{{/*
Expand the chart name.
*/}}
{{- define "adnr-edge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "adnr-edge.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label.
*/}}
{{- define "adnr-edge.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "adnr-edge.labels" -}}
helm.sh/chart: {{ include "adnr-edge.chart" . }}
{{ include "adnr-edge.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
adnr.io/site-id: {{ required "siteId is required (e.g. edge-01)" .Values.siteId | quote }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "adnr-edge.selectorLabels" -}}
app.kubernetes.io/name: {{ include "adnr-edge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Edge workload namespace.
*/}}
{{- define "adnr-edge.namespace" -}}
{{- default "dark-noc-edge" .Values.namespace }}
{{- end }}

{{/*
Kafka bootstrap host:port for documentation / optional consumers.
*/}}
{{- define "adnr-edge.kafkaBootstrap" -}}
{{- if .Values.kafka.bootstrapServers }}
{{- .Values.kafka.bootstrapServers }}
{{- else }}
{{- $host := required "kafka.externalHost is required when clusterLogForwarder.enabled (hub Kafka Route hostname)" .Values.kafka.externalHost }}
{{- printf "%s:%v" $host .Values.kafka.port }}
{{- end }}
{{- end }}

{{/*
CLF Kafka TLS URL (scheme + host:port + topic path).
*/}}
{{- define "adnr-edge.kafkaUrl" -}}
{{- $host := required "kafka.externalHost is required when clusterLogForwarder.enabled (hub Kafka Route hostname)" .Values.kafka.externalHost }}
{{- printf "tls://%s:%v/%s" $host .Values.kafka.port .Values.kafka.topic }}
{{- end }}
