{{/*
The upstream MCP dependency merges global and local server maps. Reserve this
server for the hub even when an upgrade supplies the old enabled server entry.
This override of the mcp-servers 0.5.17 helper keeps its merge for other servers.
*/}}
{{- define "mcp-servers.mergeMcpServers" -}}
{{- $global := index (.Values.global | default dict) "mcp-servers" | default dict -}}
{{- $local := index .Values "mcp-servers" | default dict -}}
{{- omit (merge (deepCopy $global) $local) "noc-servicenow" | toJson -}}
{{- end -}}

{{/*
Keep the toolgroup default on upgrades using --reuse-values, which can discard
new chart defaults. Preserve the global/local precedence of llama-stack 0.8.7.
*/}}
{{- define "llama-stack.mergeMcpServers" -}}
{{- $global := index (.Values.global | default dict) "mcp-servers" | default dict -}}
{{- $local := index .Values "mcp-servers" | default dict -}}
{{- $servicenow := dict "noc-servicenow" (dict "uri" "http://mcp-noc-servicenow:8000/mcp") -}}
{{- merge (deepCopy $global) $local $servicenow | toJson -}}
{{- end -}}

{{- define "hub.servicenow" -}}
{{- $legacy := .Values.network -}}
{{- $shared := .Values.global.servicenow | default dict -}}
{{- $legacyMcp := dig "mcp-servers" "mcp-servers" "noc-servicenow" (dict) $legacy -}}
{{- $mock := dict "enabled" true "image" (dict "repository" "quay.io/rh-ai-quickstart/noc-servicenow-mock" "tag" "0.1.0") -}}
{{- $mcp := dict "image" (dict "repository" "quay.io/rh-ai-quickstart/noc-mcp-servicenow" "tag" "0.1.0") "imagePullPolicy" "Always" -}}
{{- $_ := mergeOverwrite $mock (get $legacy "servicenowMock" | default dict) (get $shared "mock" | default dict) -}}
{{- $_ := mergeOverwrite $mcp (pick $legacyMcp "image" "imagePullPolicy") (get $shared "mcp" | default dict) -}}
{{- $legacyCredentials := dig "mcpSecrets" "servicenow" (dict) $legacy -}}
{{- $sharedCredentials := get $shared "credentials" | default dict -}}
{{- $credentials := mergeOverwrite (dict "create" true "username" "" "password" "" "apiKey" "") $legacyCredentials -}}
{{- if $mock.enabled -}}
  {{- $_ := set $credentials "username" ($credentials.username | default "admin") -}}
  {{- $_ := set $credentials "password" ($credentials.password | default "admin") -}}
  {{- $_ := set $credentials "apiKey" ($credentials.apiKey | default "demo-api-key-2026") -}}
{{- else if and (hasKey $sharedCredentials "username") (hasKey $sharedCredentials "password") -}}
  {{- /* Explicit shared Basic auth replaces any inherited API key. */ -}}
  {{- $_ := set $credentials "apiKey" "" -}}
{{- end -}}
{{- $_ := mergeOverwrite $credentials $sharedCredentials -}}
{{- $legacyUrl := dig "env" "SERVICENOW_URL" "" $legacyMcp -}}
{{- if eq $legacyUrl "http://servicenow-mock.hub.svc:8080" -}}
  {{- /* Helm can carry the old hardcoded namespace forward with --reuse-values. */ -}}
  {{- $legacyUrl = "" -}}
{{- end -}}
{{- $url := coalesce (get $shared "url") $legacyUrl "http://servicenow-mock:8080" -}}
{{- $env := mergeOverwrite (dict "MCP_PORT" "8000" "MCP_TRANSPORT" "streamable-http") (get $legacyMcp "env" | default dict) -}}
{{- $_ := set $env "SERVICENOW_URL" $url -}}
{{- dict "mock" $mock "mcp" $mcp "credentials" $credentials "env" $env | toJson -}}
{{- end -}}

{{- define "hub.servicenowCredentialEnv" -}}
{{- range $key := list "SERVICENOW_USERNAME" "SERVICENOW_PASSWORD" "SERVICENOW_API_KEY" }}
- name: {{ $key }}
  valueFrom:
    secretKeyRef:
      name: {{ $.existingSecretName | default "noc-servicenow-secret" | quote }}
      key: {{ $key }}
      optional: true
{{- end -}}
{{- end -}}

{{- define "hub.servicenowMcpSelector" -}}
app.kubernetes.io/name: noc-servicenow
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "hub.servicenowMcpLabels" -}}
app: mcp-noc-servicenow
app.kubernetes.io/component: mcp-server
app.kubernetes.io/managed-by: {{ .Release.Service }}
mcp.transport: streamable-http
{{ include "hub.servicenowMcpSelector" . }}
{{- end -}}
