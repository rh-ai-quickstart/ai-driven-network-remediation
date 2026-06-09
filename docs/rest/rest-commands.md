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
http GET http://localhost:8000/models
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
