{{/*
Expand the name of the chart.
*/}}
{{- define "s4.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "s4.fullname" -}}
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
{{- define "s4.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "s4.labels" -}}
helm.sh/chart: {{ include "s4.chart" . }}
{{ include "s4.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: storage
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "s4.selectorLabels" -}}
app.kubernetes.io/name: {{ include "s4.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app: {{ include "s4.fullname" . }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "s4.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "s4.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Create the name of the secret to use for S3 credentials
*/}}
{{- define "s4.secretName" -}}
{{- if .Values.s3.existingSecret }}
{{- .Values.s3.existingSecret }}
{{- else }}
{{- include "s4.fullname" . }}-credentials
{{- end }}
{{- end }}

{{/*
Create the name of the configmap
*/}}
{{- define "s4.configMapName" -}}
{{- include "s4.fullname" . }}-config
{{- end }}

{{/*
Create the name of the data PVC
*/}}
{{- define "s4.dataPvcName" -}}
{{- if .Values.storage.data.existingClaim }}
{{- .Values.storage.data.existingClaim }}
{{- else }}
{{- include "s4.fullname" . }}-data
{{- end }}
{{- end }}

{{/*
Create the name of the local storage PVC
*/}}
{{- define "s4.localStoragePvcName" -}}
{{- if .Values.storage.localStorage.existingClaim }}
{{- .Values.storage.localStorage.existingClaim }}
{{- else }}
{{- include "s4.fullname" . }}-local-storage
{{- end }}
{{- end }}

{{/*
Common annotations
*/}}
{{- define "s4.annotations" -}}
{{- with .Values.commonAnnotations }}
{{ toYaml . }}
{{- end }}
{{- end }}
