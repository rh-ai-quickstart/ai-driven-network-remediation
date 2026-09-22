# RAN Remediation Service

Automated remediation for ML-detected RAN anomalies. Consumes enriched anomaly records
produced by `ran-rca-service`, selects an AAP job template by keyword-matching the LLM's
`root_cause`, executes the remediation via LlamaStack MCP + AAP, sends a Slack notification,
and publishes an audit record to `ran-remediation-results`.

---

## Where it fits in Workflow 2

```
ran-rca-service
  → Kafka: ran-anomalies-enriched
  → ran-remediation-service (LangGraph: decide → remediate → notify → audit)
      → LlamaStack /v1/tool-runtime/invoke
          → mcp-aap server
              → AAP / aap-mock (launch job, poll, get output)
      → Slack notification (optional)
      → Kafka: ran-remediation-results
```

This is the "act" layer that completes the **detect → explain → act → confirm** loop.
`ran-anomaly-detector` detects, `ran-rca-service` explains, this service acts.

---

## Input contract (`ran-anomalies-enriched` topic)

Consumes the same enriched records produced by `ran-rca-service`
(see `contracts/ran-anomaly-enriched.schema.json`):

```json
{
  "incident_id": "a3f7c2d1",
  "zone": "A",
  "application": "File",
  "kpi_window": [ /* 128 × 18 TelecomTS channels */ ],
  "ad_label": "anomalous",
  "ad_confidence": 0.9995,
  "root_cause": "Signal degradation consistent with antenna misalignment...",
  "recommended_fix": "Verify antenna tilt per vendor guide Section 4.3.2..."
}
```

---

## LangGraph pipeline

```
START → decide → remediate → notify → audit → END
```

- **`decide`** — keyword-matches `root_cause` (and `recommended_fix`) to select an AAP job
  template name and builds the `extra_vars` payload (incident_id, zone, application,
  root_cause, recommended_fix, etc.)
- **`remediate`** — calls AAP via LlamaStack MCP tools (`launch_job`, `get_job_status`,
  `get_job_output`); polls until the job reaches a terminal state; same pattern as
  Workflow 1's `agent-service/nodes/remediate.py`
- **`notify`** — sends a Slack Block Kit message (color-coded green/red); same pattern
  as Workflow 1's `agent-service/nodes/notify.py`; gated by `SLACK_BOT_TOKEN`
- **`audit`** — publishes a structured record to `ran-remediation-results` Kafka topic;
  same pattern as Workflow 1's `agent-service/nodes/audit.py`

### Root cause → template mapping (`decide` node)

Since ML-based detection produces typeless anomalies (no `anomaly_type` field), the `decide`
node keyword-matches the LLM-generated `root_cause` and `recommended_fix` text to select the
most appropriate AAP template:

| Keywords in `root_cause` / `recommended_fix` | AAP template | What the Ansible job does |
|---|---|---|
| "antenna", "tilt", "RSRP", "signal strength", "misalignment" | `ran-antenna-tilt-adjust` | Adjusts antenna downtilt/azimuth |
| "interference", "SINR", "noise", "beam" | `ran-interference-mitigation` | Power/beam adjustment to reduce interference |
| "throughput", "scheduler", "rate", "latency", "MCS" | `ran-scheduler-optimize` | Tunes RAN scheduler parameters |
| "load", "UE", "congestion", "offload", "handover" | `ran-load-balance` | Redistributes load across adjacent cells |
| "capacity", "PRB", "utilization", "expansion" | `ran-capacity-expand` | Expands PRB allocation or triggers offload |
| "failure", "outage", "recovery", "restart", "down" | `ran-cell-recovery` | Cell restart and recovery sequence |
| *(no keyword match)* | `ran-generic-remediation` | Generic fallback remediation |

Keywords are checked in priority order; the first match wins.

### How AAP is called (MCP pattern — same as Workflow 1)

`ran-remediation-service` does **not** call AAP directly. It calls LlamaStack's
`/v1/tool-runtime/invoke` endpoint, which routes the call to the `mcp-aap` server:

```python
# mcp_client.invoke_tool() — independent reimplementation of the same
# pattern used by agent-service/utils.py, no cross-service imports.
await invoke_tool("launch_job",     {"job_template_name": template, "extra_vars": {...}})
await invoke_tool("get_job_status", {"job_id": job_id})
await invoke_tool("get_job_output", {"job_id": job_id})
```

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
| `LLAMASTACK_HOST` | `llamastack-service` | LlamaStack MCP gateway |
| `LLAMASTACK_PORT` | `8321` | |
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

## What was reused vs. what is new

### Reused patterns (no code shared — independent reimplementation)

| Pattern | Source | Notes |
|---|---|---|
| MCP tool invocation via LlamaStack | `agent-service/utils.py` | Same HTTP call, own `mcp_client.py` |
| LangGraph `StateGraph` + Pydantic state | `agent-service`, `ran-rca-service` | Same library, typed `RemediationState` |
| `TopicConsumer` background thread | `shared.kafka` | Identical usage to `ran-rca-service` |
| FastAPI `/health` + `/ready` probes | All hub services | Same convention |
| Slack Block Kit notify | `agent-service/nodes/notify.py` | RAN-specific fields (incident_id/zone/application vs. pod/namespace) |
| Kafka audit publish | `agent-service/nodes/audit.py` | RAN fields, `ran-remediation-results` topic |
| Two-stage `uv` Containerfile with `hub/` context | `ran-rca-service` | Same build pattern |

### Newly built in this service

| Component | Why new |
|---|---|
| `mcp_client.py` | Independent copy of `invoke_tool` — no cross-service imports |
| `nodes/decide.py` | Root cause keyword → AAP template mapping (ML-schema aware) |
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
| MCP client | [`hub/ran-remediation-service/src/ran_remediation_service/mcp_client.py`](../hub/ran-remediation-service/src/ran_remediation_service/mcp_client.py) |
| Decide node (root cause mapping) | [`hub/ran-remediation-service/src/ran_remediation_service/nodes/decide.py`](../hub/ran-remediation-service/src/ran_remediation_service/nodes/decide.py) |
| AAP mock (seed templates) | [`hub/infra/aap-mock/main.py`](../hub/infra/aap-mock/main.py) |
| Kafka topic definition | [`hub/helm/charts/kafka/values.yaml`](../hub/helm/charts/kafka/values.yaml) |
| Helm Deployment + Service | [`hub/helm/charts/telco/templates/ran-remediation-service.yaml`](../hub/helm/charts/telco/templates/ran-remediation-service.yaml) |
| Helm values block | [`hub/helm/charts/telco/values.yaml`](../hub/helm/charts/telco/values.yaml) (`ranRemediationService:`) |
| Upstream detection | [`docs/telco-oran-anomaly-detection.md`](telco-oran-anomaly-detection.md) |
| Upstream RCA | [`docs/telco-oran-rca.md`](telco-oran-rca.md) |
