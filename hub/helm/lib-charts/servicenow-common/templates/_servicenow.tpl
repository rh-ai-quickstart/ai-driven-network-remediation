{{- define "hub.servicenowMockUrl" -}}
http://servicenow-mock:8080
{{- end -}}

{{- define "hub.servicenowLegacyMockUrl" -}}
http://servicenow-mock.hub.svc:8080
{{- end -}}

{{- define "hub.servicenowSecretName" -}}
noc-servicenow-secret
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
