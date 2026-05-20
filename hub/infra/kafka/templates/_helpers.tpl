{{/*
Derive the external route hostname.
1. Explicit override via .Values.kafka.externalRoute.host
2. Auto-discover from OpenShift ingress config (lookup)
3. Fallback for helm-template dry-runs (no cluster connection)
*/}}
{{- define "kafka.externalHost" -}}
{{- if .Values.kafka.externalRoute.host -}}
  {{- .Values.kafka.externalRoute.host -}}
{{- else if .Capabilities.APIVersions.Has "config.openshift.io/v1" -}}
  {{- $ingress := (lookup "config.openshift.io/v1" "Ingress" "" "cluster") -}}
  {{- if $ingress -}}
    {{- printf "kafka-external-%s.%s" .Release.Namespace $ingress.spec.domain -}}
  {{- else -}}
    {{- printf "kafka-external-%s.apps.cluster.local" .Release.Namespace -}}
  {{- end -}}
{{- else -}}
  {{- printf "kafka-external-%s.apps.cluster.local" .Release.Namespace -}}
{{- end -}}
{{- end -}}
