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

### Faulty RF Filters (Temporal) demo

The `rf_filter_fault` scenario is a 128 × 18 KPI window from the pinned TelecomTS
dataset revision `1b3a88a440f4922edfcd1e230e42c003c0e13bdf` (first matching
sample, shard 0, row 283). Its source label is `Faulty RF Filters (Temporal)`.
Run `make generate-fixtures` to generate
`hub/telco-oran/src/telco_oran/fixtures/rf_filter_fault.json` before local tests
or demo use. The RAN chatbot image build generates fixtures automatically.

In Demo Mode, select **Faulty RF Filters (Temporal)**. The sample enters the usual
`ran-combined-metrics` topic. If Mantis AD reports `anomalous` and Mantis RCA
returns `Faulty RF Filters (Temporal)`, RCA assigns `rf_filter_fault`. Remediation
selects only `ran-rf-filter-response`; an unavailable template fails the launch
instead of falling back to `ran-generic-remediation`.

The AAP mock template returns a simulated RF filter inspection and maintenance
dispatch in job stdout, followed by a successful result and `changed=0`. It does
not call a RAN device. The same incident ID links the label and category in
`GET /api/anomalies` to `remediation_template_name`, `remediation_job_status`,
and `remediation_output_summary`; the incident card displays those fields.
The public flow is checked by `test_demo_trigger_rf_filter_publishes_controlled_window`,
`test_rf_filter_window_keeps_mantis_category_when_explanation_fails`, and
`test_rf_filter_incident_reaches_distinct_mock_job_api_and_card`. The tests feed
an anomalous KPI record and stub the classifier response; they do not measure
live model accuracy.

For a deployed model check, run the opt-in
`hub/integration-tests/tests/telco/test_rca_real_e2e.py` flow with
`RCA_FIXTURE_SCENARIO=rf_filter_fault` and `RCA_REAL_E2E=1` after generating
fixtures. It verifies the live label, `rf_filter_fault`, selected mock template,
job output, and joined anomalies API record under the trigger's incident ID.

### Doppler Shift (Severe) demo

The checked-in `doppler_shift_severe` fixture contains a controlled 128 × 18
TelecomTS KPI window with source label `Doppler Shift (Severe)`. Select that
scenario in Demo Mode to send the window through the normal anomaly flow. When
Mantis AD marks it `anomalous` and Mantis RCA returns the source label, RCA
assigns the exact `doppler_shift` category. Remediation selects
`ran-doppler-shift-response`; if that template is unavailable, the launch fails
without selecting `ran-generic-remediation`.

The AAP mock job reports a simulated Doppler frequency-offset tracking and
compensation plan, a successful result, and `changed=0` in its stdout. It never
changes a RAN device. `GET /api/anomalies` joins the original label, category,
selected job, status, and output under the same incident ID; the incident card
shows those fields and the action result. The local public-flow checks are
`test_doppler_window_keeps_its_mantis_category_when_explanation_fails` and
`test_doppler_incident_reaches_frequency_tracking_mock_job_api_and_card`. They
use the controlled window and a stubbed classifier response; they do not test
live model accuracy.

For a deployed check against the pinned predictor, run the opt-in
`hub/integration-tests/tests/telco/test_rca_real_e2e.py` flow with
`RCA_FIXTURE_SCENARIO=doppler_shift_severe` and `RCA_REAL_E2E=1`. It verifies
the model label, category, mock job result, and anomalies API join using the
triggered incident ID.

### Faulty Handover Algorithm (Too Frequent) demo

The `handover_fault` fixture is a controlled 128 × 18 KPI window from the pinned
TelecomTS revision `1b3a88a440f4922edfcd1e230e42c003c0e13bdf` (first
matching sample, shard 0, row 373). Its source label is `Faulty Handover
Algorithm (Too Frequent)`. Run `make generate-fixtures` before local tests or
demo use; the RAN chatbot image build generates fixtures automatically. Select
that scenario in Demo Mode to publish the window to
`ran-combined-metrics`. When Mantis AD marks it anomalous and Mantis RCA returns
the source label, RCA assigns exactly `handover_fault`. Remediation selects
`ran-handover-stabilization`; a missing template fails launch without choosing
`ran-generic-remediation`.

The AAP mock job reports simulated handover threshold and timer tuning to reduce
excessive handovers, a successful result, and `changed=0`. It does not change a
RAN device. `GET /api/anomalies` joins the original label, category, selected
job, status, and output under one incident ID; the incident card shows them.
Local public-flow checks are
`test_handover_window_publishes_exact_mantis_category` and
`test_handover_incident_reaches_stabilization_mock_job_api_and_card`. They use a
controlled window and a stubbed classifier response, so they do not measure
live model accuracy.

For a deployed predictor check, run the opt-in
`hub/integration-tests/tests/telco/test_rca_real_e2e.py` flow with
`RCA_FIXTURE_SCENARIO=handover_fault` and `RCA_REAL_E2E=1`. It checks the live
label, category, mock job output, and anomalies API join using the trigger's
incident ID.

### Buffer Overflow (Gradual Buildup) demo

The `buffer_overflow` fixture is a controlled 128 × 18 KPI window from the pinned
TelecomTS revision `1b3a88a440f4922edfcd1e230e42c003c0e13bdf` (first
matching sample, shard 0, row 447). Run `make generate-fixtures` before local
tests or demo use; the RAN chatbot image build generates it automatically. In
Demo Mode, select **Buffer Overflow (Gradual Buildup)** to publish the window
to `ran-combined-metrics`.

When Mantis AD marks the window anomalous and Mantis RCA returns its source
label, RCA assigns exactly `buffer_overflow`. Remediation selects
`ran-buffer-relief`; a missing template fails launch without choosing the
generic fallback. The AAP mock job reports a simulated uplink queue drain and
traffic shaping plan, a successful result, and `changed=0`. It does not change
a RAN device. `GET /api/anomalies` joins the original label, category, selected
job, ID, status, and output under the trigger's incident ID; the incident card
shows the same fields.

Local checks are `test_demo_trigger_buffer_overflow_publishes_controlled_window`,
`test_buffer_overflow_window_publishes_exact_mantis_category`, and
`test_buffer_overflow_incident_reaches_buffer_relief_mock_job_api_and_card`.
They use the controlled window and stubbed classifier response, so they do not
measure live model accuracy. For a deployed predictor check, run the opt-in
`hub/integration-tests/tests/telco/test_rca_real_e2e.py` flow with
`RCA_FIXTURE_SCENARIO=buffer_overflow` and `RCA_REAL_E2E=1`.

### High Network Congestion demos

Demo Mode offers **High Network Congestion (Gradual Buildup)** and **High
Network Congestion (Sudden Spike)**. Both send controlled 128 × 18 TelecomTS
KPI windows through `ran-combined-metrics`; the gradual sample is generated
from the pinned TelecomTS revision by `make generate-fixtures`. When Mantis AD
marks either window anomalous and Mantis RCA returns its source label, RCA
retains the full original label in `ml_class` and assigns the shared
`congestion` category. Remediation selects the existing `ran-load-balance`
template for either label, without selecting `ran-generic-remediation`.

The AAP mock reports a simulated UE redistribution plan across adjacent cells,
`successful` job status, and `changed=0`. The result says that no RAN device
changed. `GET /api/anomalies` joins each label, category, template, job ID,
status, and output under its incident ID. The incident card displays the
original label and synthetic job result, so gradual and sudden incidents remain
distinct even though they share one action.

Local public-flow checks cover both labels in
`test_demo_trigger_congestion_labels_publish_controlled_windows`,
`test_congestion_window_publishes_original_mantis_label_and_shared_category`,
and `test_congestion_labels_share_load_balance_mock_job_api_and_card`. They use
controlled windows and stubbed classifier responses, so they verify routing
and presentation rather than live model accuracy. For deployed predictor checks,
run the opt-in `hub/integration-tests/tests/telco/test_rca_real_e2e.py` flow
separately with `RCA_FIXTURE_SCENARIO=high_congestion_gradual` and
`RCA_FIXTURE_SCENARIO=high_congestion_sudden`, plus `RCA_REAL_E2E=1`.

The RCA model supports only its ten-class TelecomTS input population. Inputs
outside that population, such as Jamming, can receive a confident but wrong
supported label; confidence alone cannot identify them. The current binary
anomaly detector does not exclude Jamming before RCA. See the
[RCA model card](../model-serving/ran-rca-ml-service/rca-model-card.md) for
the validation limits.

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
