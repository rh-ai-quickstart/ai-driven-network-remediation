# Rest Commands

## MaaS

```bash
curl -vk -H "Authorization: Bearer $ADNR_LLM_TOKEN" \
  "$ADNR_LLM_URL/models"
```

## Ingestion Pipeline

1. Access to the service

```bash
oc port-forward -n hub-fabio svc/hub-ingestion-pipeline 8000:8000
```

2. Get the models

```bash
http GET http://localhost:8000/models
```

