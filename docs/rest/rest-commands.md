# Rest Commands

## MaaS

```bash
curl -vk -H "Authorization: Bearer $ADNR_LLM_TOKEN" \
  "$ADNR_LLM_URL/models"
```

## Ingestion Pipeline

1. Access to the service

```bash
oc port-forward -n $NAMESPACE svc/hub-ingestion-pipeline 8000:8000
```

2. Get the models

```bash
http http://localhost:8000/models
```

3. Get the vector stores

```bash
http http://localhost:8000/vector-store
```

## AAP Tool

```bash
oc port-forward -n $NAMESPACE svc/mcp-noc-aap 8004:8000
```

```bash
http POST http://localhost:8004/mcp \
  Accept:'application/json, text/event-stream' \
  Content-Type:application/json \
  jsonrpc=2.0 \
  id:=1 \
  method=tools/call \
  params:='{"name":"list_job_templates","arguments":{}}'
```

## LlamaStack (OGX)

```bash
oc port-forward -n $NAMESPACE svc/llamastack-service 8321:8321
```

```bash
http "http://localhost:8321/v1/models"
```

```bash
http "http://localhost:8321/v1/vector_stores"
```

```bash
http "http://localhost:8321/v1/toolgroups"
```

```bash
http "http://localhost:8321/v1/tool-runtime/list-tools"
```

## Verify MCP Kube service binding

```bash
oc run mcp-reachability-check \
  -n "$NAMESPACE" \
  --rm -i --tty \
  --restart=Never \
  --image=curlimages/curl:8.8.0 \
  --command -- sh -lc '
    set -eu

    echo "== Service health =="
    curl -sS -i http://mcp-noc-openshift:8000/health

    echo
    echo "== MCP tools/list =="
    curl -sS -i \
      -H "Accept: application/json, text/event-stream" \
      -H "Content-Type: application/json" \
      -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}" \
      http://mcp-noc-openshift:8000/mcp
  '
```