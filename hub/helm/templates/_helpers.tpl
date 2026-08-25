{{/*
Expand the name of the chart.
*/}}
{{- define "hub.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "hub.fullname" -}}
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
Common labels
*/}}
{{- define "hub.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "hub.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "hub.selectorLabels" -}}
app.kubernetes.io/name: {{ include "hub.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Llama Stack base URL for hub services (in-namespace subchart or shared instance).
*/}}
{{- define "hub.llamastackUrl" -}}
{{- index .Values "llama-stack" "url" }}
{{- end }}

{{/*
Legacy host/port env vars for service images built before LLAMASTACK_URL.
*/}}
{{- define "hub.llamastackHost" -}}
{{- $hostPort := trimPrefix "https://" (trimPrefix "http://" (include "hub.llamastackUrl" .)) }}
{{- (splitList ":" $hostPort | first) }}
{{- end }}

{{- define "hub.llamastackPort" -}}
{{- $hostPort := trimPrefix "https://" (trimPrefix "http://" (include "hub.llamastackUrl" .)) }}
{{- (splitList ":" $hostPort | last) }}
{{- end }}

{{/*
AAP controller URL from the noc-aap MCP server config.
*/}}
{{- define "hub.aapUrl" -}}
{{- index .Values "mcp-servers" "mcp-servers" "noc-aap" "env" "AAP_URL" }}
{{- end }}

{{/*
Loki gateway base URL, constructed from .Values.lokistack.name and .Values.lokistack.namespace.
Namespace defaults to the release namespace when empty.
*/}}
{{- define "hub.lokiGatewayUrl" -}}
{{- $ns := .Values.lokistack.namespace | default .Release.Namespace }}
{{- printf "https://%s-gateway-http.%s.svc:8080" .Values.lokistack.name $ns }}
{{- end }}

{{/*
ServiceAccount for an OpenShift oauth-proxy sidecar, shared by hub-frontend
and hub-ran-frontend (see global.frontendAuth.enabled). The redirect
reference annotation is what lets oauth-proxy self-register as an OAuth
client for the frontend's own Route, with no manual OAuthClient object.
Expects a dict: "component" (e.g. "frontend", "ran-frontend") and "context"
(the root chart context, i.e. $).
*/}}
{{- define "hub.oauthProxyServiceAccount" -}}
{{- $component := .component }}
{{- $ctx := .context }}
{{- $name := printf "%s-%s" (include "hub.fullname" $ctx) $component }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ $name }}
  labels:
    {{- include "hub.labels" $ctx | nindent 4 }}
    app.kubernetes.io/component: {{ $component }}
  annotations:
    serviceaccounts.openshift.io/oauth-redirectreference.primary: {{ printf "{\"kind\":\"OAuthRedirectReference\",\"apiVersion\":\"v1\",\"reference\":{\"kind\":\"Route\",\"name\":\"%s\"}}" $name | quote }}
{{- end }}

{{/*
OpenShift oauth-proxy sidecar container, shared by hub-frontend and
hub-ran-frontend. Requires an authenticated OpenShift login before letting
traffic through to nginx on localhost:8080 (the SPA + its /api/* proxy).
Stays on plain HTTP internally, same as the nginx container it fronts —
the existing Route already terminates TLS at the edge, so no additional
serving-cert/TLS plumbing is needed inside the pod. Expects a dict:
"component" (e.g. "frontend", "ran-frontend") and "context" (the root chart
context, i.e. $).
*/}}
{{- define "hub.oauthProxyContainer" -}}
{{- $component := .component }}
{{- $ctx := .context }}
{{- $name := printf "%s-%s" (include "hub.fullname" $ctx) $component }}
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

{{/*
Liveness/readiness probe body for the frontend/ran-frontend nginx container
when global.frontendAuth.enabled is true. Kubernetes httpGet probes connect to
the pod's routable IP, not loopback, so they can't be used once nginx is bound
to 127.0.0.1 only (see NGINX_LISTEN_ADDRESS in nginx.conf.template) -- exec
probes run inside the container's own network namespace instead, so
127.0.0.1 still resolves correctly regardless of what the pod's external
interface is bound to.
*/}}
{{- define "hub.nginxLoopbackProbe" -}}
exec:
  command: ["wget", "--no-verbose", "--tries=1", "--spider", "http://127.0.0.1:8080/"]
initialDelaySeconds: 5
periodSeconds: 10
{{- end }}
