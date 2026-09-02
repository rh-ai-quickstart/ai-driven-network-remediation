# ran-chatbot-service

Thin conversational entrypoint (FastAPI BFF) for the Telco O-RAN anomaly detection and root
cause analysis use case. Exposes `POST /api/chat` so operators can ask about recently detected
RAN cell anomalies, their likely root cause, and the recommended fix, in natural language. Also
exposes `GET /api/anomalies` so a UI can render the current anomaly list directly, without going
through chat; `DELETE /api/anomalies` to clear that list for a clean demo/UI state; and
`POST /api/demo/trigger` to inject a synthetic reading into the real pipeline for demos.

## Endpoints

| Path | Method | Purpose |
|---|---|---|
| `/health` | GET | Liveness probe |
| `/ready` | GET | Readiness (Kafka + LLM dependency status, always returns 200) |
| `/api/chat` | POST | Conversational reply grounded in recently detected anomalies |
| `/api/anomalies` | GET | Recent enriched anomalies (in-memory buffer), newest first |
| `/api/anomalies` | DELETE | Clear the in-memory anomaly buffer |
| `/api/demo/trigger` | POST | Publish a synthetic RAN KPI reading to `ran-combined-metrics` for demos |

**None of these endpoints check authentication or authorization themselves** — this service has no
concept of a caller identity. In production this BFF is never exposed directly (no Route of its
own); it's only reachable through `hub-ran-frontend`'s nginx `/api/` proxy, which is what
[`hub/ran-frontend`](../ran-frontend/README.md)'s "Access control" section documents: an optional
`global.frontendAuth.enabled` OpenShift `oauth-proxy` gate in front of the Route (off by default),
plus always-on nginx rate limiting that's stricter for `POST /api/demo/trigger` and
`DELETE /api/anomalies` than for the polled `GET /api/anomalies`. If this service is ever reached
another way (e.g. a direct Route, or from outside the cluster), it would need that same protection
applied at that new entry point too, since none of it lives in this service's own code.

This service is a **thin channel layer**: it does not detect anomalies or perform root cause
analysis itself. That domain logic lives in [`ran-anomaly-detector`](../ran-anomaly-detector)
(ML-based detection via [`ran-ml-service`](../ran-ml-service)) and the upstream `ran-rca-service`
(LLM root cause analysis + RAG recommended fix retrieval). This service only builds a conversational
prompt from already-enriched anomaly data and formats the LLM's reply.

This is an independent workflow/deployment from `hub/chatbot-service` (the network remediation
NOC chatbot): different domain, different Kafka topics, different persona/prompt, and it can be
enabled/disabled separately in Helm. The two services do share one thing: a handful of
domain-free infrastructure helpers (`utc_now`, `normalize_session_id`, `build_deps`, `probe_http`)
factored out into [`hub/shared`](../shared/) (`shared.utils` / `shared.probes`), a local package
depended on via a `uv` path source, so fixes to that plumbing aren't duplicated across both
services. `hub/shared` is also used by `ran-anomaly-detector`, `ran-rca-service`, and
`agent-service` for its Kafka consumer and RAG client modules, unrelated to this service.

## Where the anomaly data comes from

[`kafka.py`](src/ran_chatbot_service/kafka.py)'s `AnomaliesConsumer` is a single background thread,
started at app startup (see the `lifespan` in
[`__init__.py`](src/ran_chatbot_service/__init__.py)), that owns the Kafka connection to
`ENRICHED_ANOMALIES_TOPIC` (`ran-anomalies-enriched` by default, see
[`config.py`](src/ran_chatbot_service/config.py)) and continuously fills an in-memory buffer
(`deque(maxlen=ENRICHED_ANOMALIES_MAX_MESSAGES)`) — the same pattern already used by
[`ran-anomaly-detector`](../ran-anomaly-detector)'s `MetricsConsumer`. Both `POST /api/chat` and
`GET /api/anomalies` just read that buffer directly: no per-request Kafka I/O, unlike the older
per-request `fetch_recent_audits()`-style approach `hub/chatbot-service` uses. On connect (and every
reconnect), it seeks each partition back a bounded window and drains it so the buffer has recent
history immediately, rather than only filling in as new anomalies trickle in. It intentionally
does **not** use a Kafka consumer group — the topic has multiple partitions, and a shared group
would split them across replicas if this service is ever scaled beyond one, so each replica stays
group-less and independently sees the full topic.

`DELETE /api/anomalies` clears that in-memory buffer directly (`deque.clear()`) for a clean
demo/UI state. It does **not** survive a restart or Kafka reconnect: `_seed_recent_history()`
re-drains the same recent window from `ran-anomalies-enriched` (7-day retention by default) on
every (re)connect, so anomalies still on that topic will resurface then.

That topic is populated by [`ran-rca-service`](../ran-rca-service) (LLM root cause analysis + RAG-
based recommended fix), which enriches each anomaly detected by
[`ran-anomaly-detector`](../ran-anomaly-detector) (via [`ran-ml-service`](../ran-ml-service) Mantis
AD) with `root_cause` and `recommended_fix`, matching this output contract
(`contracts/ran-anomaly-enriched.schema.json`):

```json
{
  "incident_id": "a3f7c2d1",
  "zone": "A",
  "application": "Twitch",
  "kpi_window": [ /* 128 × 18 TelecomTS channels */ ],
  "ad_label": "anomalous",
  "ad_confidence": 0.9995,
  "root_cause": "Signal degradation consistent with antenna misalignment...",
  "recommended_fix": "Verify antenna tilt per vendor guide Section 4.3.2..."
}
```

## Demo trigger

[`demo.py`](src/ran_chatbot_service/demo.py) loads a checked-in TelecomTS fixture from the
[`telco-oran`](../telco-oran) catalog and publishes it as a JSON sample straight to
`DEMO_METRICS_TOPIC` (`ran-combined-metrics` by default) — the same real input topic real data
arrives on. This service never talks to `ran-anomaly-detector` directly: everything downstream
(ML detection -> RCA -> this service's own `AnomaliesConsumer` buffer) is the already-running real
pipeline.

Available scenarios (from `hub/telco-oran/src/telco_oran/fixtures/`):

| Scenario | Expected AD result | TelecomTS class |
|---|---|---|
| `antenna_failure` (default) | Anomalous (>99% confidence) | Antenna Failure |
| `high_congestion_sudden` | Anomalous (>90%) | High Network Congestion (Sudden Spike) |
| `co_channel_interference_severe` | Anomalous (>90%) | Co-Channel Interference (Severe) |
| `doppler_shift_severe` | Anomalous (>95%) | Doppler Shift (Severe) |
| `normal_traffic` | Normal (no anomaly published) | Normal |

**Prerequisite:** `ran-anomaly-detector` AND `ran-ml-service` must be running for the trigger to
have any downstream effect — see `docs/RAN-DEMO-SCRIPT.md`.

## Usage

```bash
cd hub/ran-chatbot-service
uv sync --group dev
uv run pytest
```

`uv sync` resolves `shared` from the sibling [`hub/shared`](../shared/) directory via a `uv` path
source, so it must exist alongside this one (already true within this repo checkout). For the
same reason, the container image's build context is `hub/`, not this directory — see
`build-ran-chatbot-image` in the root `Makefile`.
