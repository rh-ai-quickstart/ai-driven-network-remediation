# RAN Remediation Service

Automated remediation for ML-detected RAN anomalies. Consumes enriched anomaly records
produced by `ran-rca-service`, maps the `root_cause_category` to an AAP job template,
executes the remediation via AAP, sends a Slack notification,
and publishes an audit record to `ran-remediation-results`.

---

## Where it fits in Workflow 2

```
ran-rca-service
  → Kafka: ran-anomalies-enriched
  → ran-remediation-service (LangGraph: decide → remediate → notify → audit)
      → AAP / aap-mock (launch job, poll, get output)
      → Slack notification (optional)
      → Kafka: ran-remediation-results
```

This is the "act" layer that completes the **detect → explain → act → confirm** loop.
`ran-anomaly-detector` detects, `ran-rca-service` explains, this service acts.

---

## Input contract (`ran-anomalies-enriched` topic)

Consumes the enriched records produced by `ran-rca-service`. The authoritative
field list is [`contracts/ran-anomaly-enriched.schema.json`](../contracts/ran-anomaly-enriched.schema.json),
enforced by `tests/test_contract.py` — it is deliberately not duplicated here.

The field this service routes on is `root_cause_category`.

---

## LangGraph pipeline

```
START → decide → remediate → notify → audit → END
```

- **`decide`** — maps `root_cause_category` to an AAP job template name and builds the
  `extra_vars` payload (incident_id, zone, application, root_cause, recommended_fix, etc.)
- **`remediate`** — calls AAP directly (`launch_job`, `get_job_status`,
  `get_job_output`); polls until the job reaches a terminal state
- **`notify`** — sends a Slack Block Kit message (color-coded green/red); same pattern
  as Workflow 1's `agent-service/nodes/notify.py`; gated by `SLACK_BOT_TOKEN`
- **`audit`** — publishes a structured record to `ran-remediation-results` Kafka topic;
  same pattern as Workflow 1's `agent-service/nodes/audit.py`

### Root cause → template mapping (`decide` node)

ML-based detection produces typeless anomalies (no `anomaly_type` field), so `ran-rca-service`
classifies each one into a `root_cause_category` from a closed enum. The `decide` node is then
a plain lookup from that category to an AAP job template, falling back to
`ran-generic-remediation` for `unknown` or any unrecognized value.

The mapping itself is a single dict, `_CATEGORY_TO_TEMPLATE` in
[`nodes/decide.py`](../hub/ran-remediation-service/src/ran_remediation_service/nodes/decide.py) —
read it there rather than from a table here. The category enum is defined in
[`contracts/ran-anomaly-enriched.schema.json`](../contracts/ran-anomaly-enriched.schema.json)
and the template names must match the seeds in
[`hub/infra/aap-mock/main.py`](../hub/infra/aap-mock/main.py); adding a category means
touching all three.

### How AAP is called

`ran-remediation-service` calls AAP directly via `aap_client.py`.

In local development the `aap-mock` service handles these calls, returning synthetic
`"status": "successful"` responses without running any real playbook.

---

## Kafka topics

| Topic | Direction | Purpose |
|---|---|---|
| `ran-anomalies-enriched` | Consumed | Input — enriched anomaly records from `ran-rca-service` |
| `ran-remediation-results` | Produced | Audit trail — one record per remediation attempt |

The service uses consumer group `ran-remediation-service` so each anomaly is
processed **exactly once** even across replicas.

### `ran-remediation-results` record shape

```json
{
  "timestamp":          "2026-09-21T10:00:00Z",
  "incident_id":        "a3f7c2d1",
  "zone":               "A",
  "application":        "File",
  "ad_label":           "anomalous",
  "ad_confidence":      0.9995,
  "root_cause":         "Signal degradation consistent with antenna misalignment...",
  "template_name":      "ran-antenna-tilt-adjust",
  "job_id":             "7",
  "job_status":         "successful",
  "success":            true,
  "timed_out":          false,
  "output_summary":     "PLAY RECAP: cell-tower-01 ok=3 changed=2 unreachable=0 failed=0",
  "total_duration_ms":  1500.0
}
```

---

## Endpoints

| Path | Purpose |
|---|---|
| `GET /health` | Liveness probe |
| `GET /ready` | Readiness (Kafka consumer thread alive) |
| `GET /remediation-results` | Recent remediation records (in-memory buffer) |

---

## Config (env vars)

| Variable | Default | Notes |
|---|---|---|
| `AAP_URL` | `http://aap-mock:8080` | AAP controller base URL (mock by default) |
| `AAP_TOKEN` | *(empty)* | AAP auth token; unset against the mock |
| `KAFKA_BOOTSTRAP` | `kafka:9092` | |
| `KAFKA_ENRICHED_TOPIC` | `ran-anomalies-enriched` | Input topic |
| `KAFKA_REMEDIATION_TOPIC` | `ran-remediation-results` | Output/audit topic |
| `KAFKA_GROUP_ID` | `ran-remediation-service` | Consumer group |
| `KAFKA_CONSUMER_ENABLED` | `true` | Set `false` for local runs without Kafka |
| `SLACK_ENABLED` | `false` | Set `true` to enable Slack notifications |
| `SLACK_BOT_TOKEN` | *(empty)* | Required when `SLACK_ENABLED=true` |
| `SLACK_CHANNEL` | `#ran-alerts` | |
| `POLL_INTERVAL_SECONDS` | `5` | AAP job poll cadence |
| `JOB_TIMEOUT_SECONDS` | `120` | Max wait per job |
| `RECENT_RESULTS_LIMIT` | `100` | In-memory buffer size for `/remediation-results` |

---

## Reused patterns (no code shared — independent reimplementation)

Where this service mirrors an existing one, it reimplements the pattern rather than
importing it. That is deliberate: hub services stay independently deployable and do not
take cross-service Python dependencies on each other.

| Pattern | Source | Notes |
|---|---|---|
| LangGraph `StateGraph` + Pydantic state | `agent-service`, `ran-rca-service` | Same library, typed `RemediationState` |
| `TopicConsumer` background thread | `shared.kafka` | Identical usage to `ran-rca-service` |
| FastAPI `/health` + `/ready` probes | All hub services | Same convention |
| Slack Block Kit notify | `agent-service/nodes/notify.py` | RAN-specific fields (incident_id/zone/application vs. pod/namespace) |
| Kafka audit publish | `agent-service/nodes/audit.py` | RAN fields, `ran-remediation-results` topic |
| Two-stage `uv` Containerfile with `hub/` context | `ran-rca-service` | Same build pattern |

### Newly built in this service

| Component | Why new |
|---|---|
| `aap_client.py` | Direct AAP HTTP client — no MCP indirection |
| `nodes/decide.py` | Root cause category → AAP template mapping (ML-schema aware) |
| `ran-remediation-results` Kafka topic | New audit topic for Workflow 2 |
| `ranRemediationService` Helm block | New `enabled` toggle in telco chart |
| 7 AAP seed templates in `aap-mock` | RAN-specific job templates added to existing mock |


---

## Local dev

```bash
cd hub/ran-remediation-service
uv sync --group dev
uv run ran-remediation-service          # runs one sample anomaly through the graph (no Kafka)
uv run pytest                           # full test suite
```

For a full end-to-end test with Kafka and the demo trigger:

```bash
# In one terminal — start all services (Kafka, LlamaStack, ran-anomaly-detector,
# ran-rca-service, this service)
make deploy-local

# In another terminal — inject a synthetic anomaly
curl -X POST http://localhost:8003/api/demo/trigger \
     -H 'Content-Type: application/json' \
     -d '{"scenario": "low_signal"}'

# Watch remediation results
curl http://localhost:8004/remediation-results
```

---

## Where to find things

| What | Where |
|---|---|
| Service source | [`hub/ran-remediation-service/`](../hub/ran-remediation-service/) |
| LangGraph graph | [`hub/ran-remediation-service/src/ran_remediation_service/graph.py`](../hub/ran-remediation-service/src/ran_remediation_service/graph.py) |
| AAP client | [`hub/ran-remediation-service/src/ran_remediation_service/aap_client.py`](../hub/ran-remediation-service/src/ran_remediation_service/aap_client.py) |
| Decide node (root cause mapping) | [`hub/ran-remediation-service/src/ran_remediation_service/nodes/decide.py`](../hub/ran-remediation-service/src/ran_remediation_service/nodes/decide.py) |
| AAP mock (seed templates) | [`hub/infra/aap-mock/main.py`](../hub/infra/aap-mock/main.py) |
| Kafka topic definition | [`hub/helm/charts/kafka/values.yaml`](../hub/helm/charts/kafka/values.yaml) |
| Helm Deployment + Service | [`hub/helm/charts/telco/templates/ran-remediation-service.yaml`](../hub/helm/charts/telco/templates/ran-remediation-service.yaml) |
| Helm values block | [`hub/helm/charts/telco/values.yaml`](../hub/helm/charts/telco/values.yaml) (`ranRemediationService:`) |
| Upstream detection | [`docs/telco-oran-anomaly-detection.md`](telco-oran-anomaly-detection.md) |
| Upstream RCA | [`docs/telco-oran-rca.md`](telco-oran-rca.md) |
