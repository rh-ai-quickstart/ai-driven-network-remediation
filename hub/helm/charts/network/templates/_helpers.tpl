{{- define "network.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "network.fullname" -}}
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

{{- define "network.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "network.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "network.selectorLabels" -}}
app.kubernetes.io/name: {{ include "network.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "network.lokiGatewayUrl" -}}
{{- $ns := .Values.lokistack.namespace | default .Release.Namespace }}
{{- printf "https://%s-gateway-http.%s.svc:8080" .Values.lokistack.name $ns }}
{{- end }}

{{- define "network.aapUrl" -}}
{{- index .Values "mcp-servers" "mcp-servers" "noc-aap" "env" "AAP_URL" }}
{{- end }}

{{- define "network.oauthProxyServiceAccount" -}}
{{- $component := .component }}
{{- $ctx := .context }}
{{- $name := printf "%s-%s" (include "network.fullname" $ctx) $component }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ $name }}
  labels:
    {{- include "network.labels" $ctx | nindent 4 }}
    app.kubernetes.io/component: {{ $component }}
  annotations:
    serviceaccounts.openshift.io/oauth-redirectreference.primary: {{ printf "{\"kind\":\"OAuthRedirectReference\",\"apiVersion\":\"v1\",\"reference\":{\"kind\":\"Route\",\"name\":\"%s\"}}" $name | quote }}
{{- end }}

{{- define "network.oauthProxyContainer" -}}
{{- $component := .component }}
{{- $ctx := .context }}
{{- $name := printf "%s-%s" (include "network.fullname" $ctx) $component }}
- name: oauth-proxy
  image: {{ $ctx.Values.global.frontendAuth.image }}
  args:
    - --http-address=0.0.0.0:8888
    - --https-address=
    - --provider=openshift
    - --openshift-service-account={{ $name }}
    - --upstream=http://localhost:8080
    - --cookie-secret=$(COOKIE_SECRET)
    - {{ printf "--openshift-sar={\"resource\":\"namespaces\",\"verb\":\"get\",\"name\":\"%s\"}" $ctx.Release.Namespace | quote }}
    - --skip-provider-button=true
  env:
    - name: COOKIE_SECRET
      valueFrom:
        secretKeyRef:
          name: {{ $name }}-oauth
          key: cookie-secret
  ports:
    - name: public
      containerPort: 8888
      protocol: TCP
{{- end }}

{{- define "network.nginxLoopbackProbe" -}}
exec:
  command: ["wget", "--no-verbose", "--tries=1", "--spider", "http://127.0.0.1:8080/"]
initialDelaySeconds: 5
periodSeconds: 10
{{- end }}
