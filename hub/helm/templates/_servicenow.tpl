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
{{- $legacyMock := $legacy.servicenowMock | default dict -}}
{{- $sharedMock := $shared.mock | default dict -}}
{{- $mock := dict "image" (dict "repository" "quay.io/rh-ai-quickstart/noc-servicenow-mock" "tag" "0.1.0") -}}
{{- $_ := mergeOverwrite $mock $legacyMock $sharedMock -}}
{{- $mockEnabled := true -}}
{{- range $enabled := list $legacyMock.enabled $sharedMock.enabled -}}
  {{- $value := trim (toString $enabled) -}}
  {{- if and (ne $enabled nil) $value -}}
    {{- $mockEnabled = ne (lower $value) "false" -}}
  {{- end -}}
{{- end -}}
{{- $_ := set $mock "enabled" $mockEnabled -}}
{{- $mcp := dict "image" (dict "repository" "quay.io/rh-ai-quickstart/noc-mcp-servicenow" "tag" "0.1.0") "imagePullPolicy" "Always" -}}
{{- $_ := mergeOverwrite $mcp (pick $legacyMcp "image" "imagePullPolicy") (get $shared "mcp" | default dict) -}}
{{- $_ := set $mcp "targetPort" ($legacyMcp.targetPort | default 8000) -}}
{{- $legacyCredentials := dig "mcpSecrets" "servicenow" (dict) $legacy -}}
{{- $sharedCredentials := get $shared "credentials" | default dict -}}
{{- if eq $sharedCredentials.create nil -}}
  {{- $sharedCredentials = omit $sharedCredentials "create" -}}
{{- end -}}
{{- $credentials := mergeOverwrite (dict "create" true "username" "" "password" "" "apiKey" "") $legacyCredentials -}}
{{- if $mockEnabled -}}
  {{- $_ := set $credentials "username" ($credentials.username | default "admin") -}}
  {{- $_ := set $credentials "password" ($credentials.password | default "admin") -}}
  {{- $_ := set $credentials "apiKey" ($credentials.apiKey | default "demo-api-key-2026") -}}
{{- else if and $sharedCredentials.username $sharedCredentials.password -}}
  {{- /* Explicit shared Basic auth replaces any inherited API key. */ -}}
  {{- $_ := set $credentials "apiKey" "" -}}
{{- end -}}
{{- $_ := mergeOverwrite $credentials $sharedCredentials -}}
{{- if eq $credentials.create nil -}}
  {{- $_ := set $credentials "create" true -}}
{{- end -}}
{{- $_ := set $credentials "apiKey" ($credentials.apiKey | default "" | toString) -}}
{{- $legacyUrl := dig "env" "SERVICENOW_URL" "" $legacyMcp -}}
{{- if and .Release.IsUpgrade (eq $legacyUrl "http://servicenow-mock.hub.svc:8080") -}}
  {{- /* Helm can carry the old hardcoded namespace forward with --reuse-values. */ -}}
  {{- $legacyUrl = "" -}}
{{- end -}}
{{- $url := $legacyUrl -}}
{{- if hasKey $shared "url" -}}
  {{- $url = get $shared "url" -}}
{{- end -}}
{{- $url = $url | default "" | trim | trimSuffix "/" -}}
{{- if not $mockEnabled -}}
  {{- if or (not $url) (eq $url "http://servicenow-mock:8080") -}}
    {{- fail "Set global.servicenow.url (SERVICENOW_INSTANCE_URL in Make) to use a real ServiceNow instance." -}}
  {{- end -}}
  {{- $apiKey := trim $credentials.apiKey -}}
  {{- if and $credentials.create (not $credentials.existingSecretName) (not (or $apiKey (and $credentials.username $credentials.password))) -}}
    {{- fail "Set global.servicenow.credentials to an API key, a username and password, or an existing Secret name. See docs/integration-setup.md for Helm and Make settings." -}}
  {{- end -}}
{{- end -}}
{{- $env := mergeOverwrite (dict "MCP_PORT" "8000" "MCP_TRANSPORT" "streamable-http") (get $legacyMcp "env" | default dict) -}}
{{- $_ := set $env "SERVICENOW_URL" ($url | default "http://servicenow-mock:8080") -}}
{{- $credentialEnv := dict "secretName" ($credentials.existingSecretName | default "noc-servicenow-secret") -}}
{{- $mcpCredentialEnv := merge (dict "sharedSecretName" $sharedCredentials.existingSecretName "mappings" ($legacyMcp.envSecrets | default dict)) $credentialEnv -}}
{{- dict "mock" $mock "mcp" $mcp "credentials" $credentials "env" $env "credentialEnv" $credentialEnv "mcpCredentialEnv" $mcpCredentialEnv | toJson -}}
{{- end -}}

{{- define "hub.servicenowCredentialAnnotation" -}}
{{- if and .create (not .existingSecretName) -}}
annotations:
  checksum/servicenow-credentials: {{ . | toJson | sha256sum | quote }}
{{- end -}}
{{- end -}}

{{- define "hub.servicenowCredentialEnv" -}}
{{- range $key := list "SERVICENOW_USERNAME" "SERVICENOW_PASSWORD" "SERVICENOW_API_KEY" }}
{{- $mapping := index ($.mappings | default dict) $key | default dict }}
- name: {{ $key }}
  valueFrom:
    secretKeyRef:
      name: {{ coalesce $.sharedSecretName $mapping.name $.secretName | quote }}
      key: {{ $mapping.key | default $key | quote }}
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
