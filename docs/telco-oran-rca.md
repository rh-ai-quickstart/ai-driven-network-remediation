# RAN RCA Service

LLM-based root cause analysis for ML-detected RAN anomalies. Consumes typeless anomalies
detected by `ran-anomaly-detector` (via `ran-ml-service` Mantis AD), enriches them with a
root cause and recommended fix using RAG + IBM Granite, and publishes the enriched records.

## Data flow

```
ran-anomaly-detector (ML-detected anomaly)
  → Kafka: ran-anomalies
  → ran-rca-service (LangGraph: rag_retrieval → analyze)
  → Kafka: ran-anomalies-enriched
```

- **`rag_retrieval`** — queries the `telco_oran_docs` vector store via LlamaStack for
  relevant documentation snippets, using zone + application + AD confidence as context
- **`analyze`** — sends anomaly context to Granite LLM, returns a structured
  `root_cause_category`, `root_cause`, and `recommended_fix`; falls back to
  `unknown` and empty fields on LLM failure

## Contracts

| Topic | Direction | Schema |
|---|---|---|
| `ran-anomalies` | Consumed | [`contracts/ran-anomalies.schema.json`](../contracts/ran-anomalies.schema.json) |
| `ran-anomalies-enriched` | Produced | [`contracts/ran-anomaly-enriched.schema.json`](../contracts/ran-anomaly-enriched.schema.json) |

The output is the input record plus the three fields this service adds:
`root_cause_category`, `root_cause`, and `recommended_fix`. The schemas are the
authoritative field lists and are validated in `tests/test_analyze.py` and
`tests/test_graph.py`, so they are deliberately not duplicated here.

`root_cause_category` is a closed enum — `ran-remediation-service` maps it
directly to an AAP job template, so adding a value requires a matching template
on the remediation side.

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
