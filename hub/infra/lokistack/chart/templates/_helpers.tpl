{{/*
Expand the name of the chart.
*/}}
{{- define "lokistack.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "lokistack.fullname" -}}
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
Create chart name and version as used by the chart label.
*/}}
{{- define "lokistack.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels for a component. Pass a dict with "root" (context) and "component" (string).
Usage: {{- include "lokistack.componentLabels" (dict "root" . "component" "grafana") | nindent 4 }}
*/}}
{{- define "lokistack.componentLabels" -}}
helm.sh/chart: {{ include "lokistack.chart" .root }}
app.kubernetes.io/name: {{ include "lokistack.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- if .root.Chart.AppVersion }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{- end }}

{{/*
Selector labels for a component.
Usage: {{- include "lokistack.componentSelectorLabels" (dict "root" . "component" "grafana") | nindent 6 }}
*/}}
{{- define "lokistack.componentSelectorLabels" -}}
app: {{ .component }}
app.kubernetes.io/name: {{ include "lokistack.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Get the namespace to use.
*/}}
{{- define "lokistack.namespace" -}}
{{- .Release.Namespace }}
{{- end }}

{{/*
Get the Loki gateway base URL.
*/}}
{{- define "lokistack.lokiGatewayUrl" -}}
{{- printf "https://%s-gateway-http.%s.svc:8080/api/logs/v1" .Values.lokistack.name .Release.Namespace }}
{{- end }}

{{/*
Get the OpenShift cluster apps domain from the Ingress config resource.
Falls back to .Values.clusterDomain when lookup is unavailable (helm template, non-OpenShift).
*/}}
{{- define "lokistack.clusterDomain" -}}
{{- $ingress := (lookup "config.openshift.io/v1" "Ingress" "" "cluster") }}
{{- if $ingress }}
{{- $ingress.spec.domain }}
{{- else }}
{{- .Values.clusterDomain | default "apps.example.com" }}
{{- end }}
{{- end }}

{{/*
Get the Grafana route hostname.
*/}}
{{- define "grafana.route.host" -}}
{{- printf "grafana-%s.%s" (include "lokistack.namespace" .) (include "lokistack.clusterDomain" .) }}
{{- end }}
