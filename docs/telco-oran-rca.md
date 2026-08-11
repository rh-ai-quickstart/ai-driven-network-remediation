# RAN RCA Service

LLM-based root cause analysis for RAN anomalies. Consumes anomalies detected by
`ran-anomaly-detector`, enriches them with a root cause and recommended fix using
RAG + IBM Granite, and publishes the enriched records.

## Data flow

```
ran-anomaly-detector
  → Kafka: ran-anomalies
  → ran-rca-service (LangGraph: rag_retrieval → analyze)
  → Kafka: ran-anomalies-enriched
```

- **`rag_retrieval`** — queries the `telco_oran_docs` vector store via LlamaStack for
  relevant documentation snippets
- **`analyze`** — sends anomaly + RAG context to Granite LLM, returns structured
  `root_cause` and `recommended_fix`; falls back to empty fields on LLM failure

## Endpoints

| Path | Purpose |
|---|---|
| `GET /health` | Liveness probe |
| `GET /ready` | Readiness (Kafka consumer thread alive) |
| `GET /anomalies` | Recent enriched anomalies (in-memory buffer) |

## Config (env vars)

| Variable | Default |
|---|---|
| `LLAMASTACK_HOST` | `llamastack-service` |
| `LLAMASTACK_PORT` | `8321` |
| `VECTOR_STORE_NAME` | `telco_oran_docs` |
| `GRANITE_MODEL_NAME` | `ibm-granite/granite-3.3-8b-instruct` |
| `KAFKA_BOOTSTRAP` | `kafka:9092` |
| `KAFKA_ANOMALIES_TOPIC` | `ran-anomalies` |
| `KAFKA_ENRICHED_TOPIC` | `ran-anomalies-enriched` |
| `KAFKA_GROUP_ID` | `ran-rca-service` |
| `KAFKA_CONSUMER_ENABLED` | `true` |
| `RECENT_ANOMALIES_LIMIT` | `100` |

## Local dev

```bash
cd hub/ran-rca-service
uv sync --group dev
uv run ran-rca-service          # runs a single anomaly through the graph (no Kafka)
uv run pytest                   # full test suite
```
