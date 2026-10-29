# RAN RCA Service

LLM-based root cause analysis for ML-detected RAN anomalies. Consumes typeless anomalies
detected by `ran-anomaly-detector` (via `ran-ml-service` Mantis AD), enriches them with a
root cause and recommended fix using RAG + IBM Granite, and publishes the enriched records.

## Data flow

```
ran-anomaly-detector (ML-detected anomaly)
  → Kafka: ran-anomalies
  → ran-rca-service (LangGraph: classify → rag_retrieval → analyze)
  → Kafka: ran-anomalies-enriched
```

- **`classify`** — obtains the Mantis RCA prediction as `root_cause_category` and
  validates its label, class index, and confidence
- **`rag_retrieval`** — queries the `telco_oran_docs` vector store via LlamaStack for
  relevant documentation snippets, using the predicted label and anomaly context
- **`analyze`** — sends anomaly context to Granite LLM for `root_cause` and
  `recommended_fix`; the LLM cannot change the predicted label. An LLM
  failure publishes empty explanation fields and an incident status while the
  classified anomaly continues to remediation. A RAG failure still stops enrichment

## Contracts

| Topic | Direction | Schema |
|---|---|---|
| `ran-anomalies` | Consumed | [`contracts/ran-anomalies.schema.json`](../contracts/ran-anomalies.schema.json) |
| `ran-anomalies-enriched` | Produced | [`contracts/ran-anomaly-enriched.schema.json`](../contracts/ran-anomaly-enriched.schema.json) |
| `ran-incident-status` | Produced for classification or explanation failure | [`contracts/ran-incident-status.schema.json`](../contracts/ran-incident-status.schema.json) |

The output is the input record plus `root_cause_category`, `ml_confidence`,
`root_cause`, and `recommended_fix`. The two confidence fields distinguish RCA
from anomaly detection. The schemas are the authoritative field lists and are
validated in `tests/test_analyze.py` and
`tests/test_graph.py`, so they are deliberately not duplicated here.

The Mantis endpoint returns the exact TelecomTS class label as
`root_cause_category`, alongside its class index and confidence. RCA carries
that label through to the enriched record without an intermediate category
mapping or a separate `ml_class` field. `root_cause_category` is a closed enum
of the ten predicted labels. `ran-remediation-service` selects a distinct AAP
playbook for each label, so adding a label requires a matching playbook.
For example, mild and severe co-channel interference select different
interference templates; gradual and sudden congestion select different
congestion templates.

Classification can fail even when the endpoint is reachable, for example if it
returns a mismatched class index or invalid confidence. A classification failure
stops enrichment, so there is no enriched record for the chatbot or UI to show.
The separate status record preserves the incident ID and failure reason for the
operator. An explanation failure has a different outcome: remediation continues
with empty `root_cause` and `recommended_fix` fields, and its status record
explains why they are empty.

## Endpoints

| Path | Purpose |
|---|---|
| `GET /health` | Liveness probe |
| `GET /ready` | Readiness (Kafka consumer and configured classifier model) |
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
