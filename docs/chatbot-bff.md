# Chatbot BFF — API Backend for NOC Dashboard

**Milestone:** V1 | **Tasks:** #14, #15 | **Updated:** Jun 21, 2026

---

## What It Is

A FastAPI service that aggregates operational state and exposes it as a polling API for the React dashboard (not yet built). It does **not** run remediation or call MCP tools for actions.

Communication with the agent is **Kafka-only**:
- Publishes demo triggers → `system-alerts`
- Reads audit results ← `incident-audit`

---

## Architecture

```
[React Dashboard] ──polls──► [Chatbot BFF :8080]
                                  ├── probes ──► 6 MCP servers (/health)
                                  ├── reads  ──► Kafka: incident-audit
                                  ├── queries ─► ServiceNow (open tickets)
                                  ├── calls  ──► Granite/vLLM (chat)
                                  └── writes ──► Kafka: system-alerts (demo)

[Agent Service] ──consumes──► Kafka: system-alerts
       └── writes ──► Kafka: incident-audit
```

---

## API Contract

### Dependency Status Envelope (`_deps`)

All data endpoints (`/api/*`) include a `_deps` field that reports whether the backend dependencies required by that endpoint were reachable. This allows consumers to distinguish "zero data" from "dependency unavailable" with a single consistent check.

```jsonc
// All deps healthy — data is complete
{"_deps": {"status": "ok"}, ...}

// Some deps unavailable — data is partial/fallback
{"_deps": {"status": "degraded", "unavailable": ["kafka", "servicenow"]}, ...}
```

| Endpoint | `_deps.status: "ok"` when | `_deps.status: "degraded"` when | HTTP 502 when |
|---|---|---|---|
| `GET /api/summary` | ServiceNow responded | ServiceNow unreachable | — |
| `GET /api/integrations` | All probes + Kafka ok | Any probe down or Kafka unreachable | — |
| `POST /api/chat` | LLM responded | LLM unreachable (fallback reply) | — |
| `POST /api/demo/trigger` | Kafka published | — | Kafka down (already correct) |

Infrastructure probes (`/health`, `/ready`) do **not** include `_deps`.

Frontend usage:
```js
if (data._deps.status === "degraded") {
  showBanner(data._deps.unavailable);
}
```

### `GET /health`
Liveness probe. Always returns 200.
```json
{"status": "ok", "service": "noc-chatbot-bff", "version": "0.1.0"}
```

### `GET /ready`
Readiness probe. Always returns 200 (the BFF gracefully degrades when deps are down). Reports dependency connectivity as informational data.
```json
{"status": "ready", "checks": {"kafka": true, "servicenow": true}}
```

### `GET /api/summary`
```json
{
  "_deps": {"status": "ok"},
  "timestamp": "2026-06-18T10:00:00+00:00",
  "agent_status": "running",
  "cluster": "hub",
  "site": "edge-01",
  "open_incidents": 0,
  "servicenow": {"mode": "mock", "reachable": true}
}
```

### `GET /api/integrations`
Cached (10s TTL). Probes run in parallel.
```json
{
  "_deps": {"status": "degraded", "unavailable": ["probes"]},
  "timestamp": "...",
  "total": 7,
  "up": 6,
  "down": 1,
  "slo": {
    "window_hours": 24,
    "sample_size": 0,
    "mttd_seconds": null,
    "mttr_seconds": null,
    "auto_remediation_pct": null,
    "platform_availability_pct": 85.71
  },
  "incident_movie": [],
  "business_impact": {"incidents_processed": 0, "hours_returned_to_ops": 0},
  "integrations": [
    {"id": "mcp-openshift", "name": "MCP OpenShift", "group": "mcp", "status": "up", "http_code": 200}
  ]
}
```

### `POST /api/chat`
**Request:**
```json
{"message": "What is the current status?", "session_id": "optional-uuid"}
```
**Response:**
```json
{
  "_deps": {"status": "ok"},
  "session_id": "uuid",
  "timestamp": "...",
  "reply": "Summary:\n- Site: edge-01 | Open incidents: 0\n...",
  "model": {
    "name": "granite-4-h-tiny",
    "source": "live | fallback | unreachable | disabled",
    "framework": "LangGraph + MCP"
  },
  "context": {
    "open_incidents": 0,
    "site": "edge-01",
    "integrations_up": 6,
    "integrations_total": 7
  },
  "mcp_status": [
    {"id": "mcp-openshift", "name": "MCP OpenShift", "group": "mcp", "status": "up", "http_code": 200}
  ]
}
```

### `POST /api/demo/trigger`
**Request:**
```json
{"scenario": "oom | crashloop | lightspeed | escalation", "site": "edge-01"}
```
**Response (200):**
```json
{
  "_deps": {"status": "ok"},
  "timestamp": "...",
  "status": "queued",
  "incident_id": "uuid",
  "scenario": "oom",
  "site": "edge-01",
  "topic": "system-alerts",
  "kafka_offset": 1,
  "event_message": "OOMKilled: container exceeded memory limits"
}
```
**Response (502 — Kafka failure):**
```json
{
  "timestamp": "...",
  "status": "error",
  "error": "NoBrokersAvailable",
  "incident_id": "uuid",
  "scenario": "oom",
  "site": "edge-01"
}
```

---

## Kafka Schemas

The `contracts/` directory at the repo root contains JSON Schema definitions for the Kafka event payloads shared between the chatbot BFF and the agent service. These schemas formalize what was previously implicit in source code, giving both teams a single source of truth to validate against and catch schema drift early. No runtime validation is enforced yet — they serve as documentation and a reference for future integration tests.

| File | Topic | Producer | Consumer |
|------|-------|----------|----------|
| `contracts/nginx-logs.schema.json` | `system-alerts` | Chatbot BFF (demo) / Edge forwarders | Agent Service |
| `contracts/incident-audit.schema.json` | `incident-audit` | Agent Service | Chatbot BFF |

---

## Module Structure

```
hub/chatbot-service/src/chatbot_service/
├── __init__.py    # FastAPI app, endpoints, caching
├── config.py      # Env vars, probe targets, constants
├── probes.py      # HTTP health probing, ServiceNow query
├── kafka.py       # Audit consumer, demo event producer
├── slo.py         # SLO metrics, incident timeline, business impact
├── chat.py        # LLM prompt building, model call, fallback
└── utils.py       # Shared helpers (get_mcp_items, parse_iso, etc.)
```

---

## Configuration (Environment Variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |
| `SSL_CA_BUNDLE` | *(empty = system default)* | CA bundle path, or `false` to disable TLS verify |
| `KAFKA_BOOTSTRAP` | `kafka:9092` | Kafka broker address |
| `DEMO_TOPIC` | `system-alerts` | Topic for demo trigger events |
| `AUDIT_TOPIC` | `incident-audit` | Topic for agent audit records |
| `MODEL_API_URL` | `http://llamastack-service:8321/v1/completions` | LLM endpoint |
| `SERVICENOW_URL` | `http://servicenow-mock:8080` | ServiceNow instance URL |
| `SERVICENOW_MODE` | `mock` | `mock` or `real` |
| `BASELINE_MANUAL_MTTR_SECONDS` | `900` | Manual MTTR baseline for cost savings calc |
| `OPS_HOURLY_COST_USD` | `120` | Ops hourly rate for cost savings calc |

---

## Demo Flow (V1 Happy Path)

1. User clicks demo button → `POST /api/demo/trigger` → BFF publishes event with `incident_id` to Kafka
2. Agent consumes from `system-alerts`, runs LangGraph, writes result to `incident-audit`
3. Dashboard polls `GET /api/integrations` → BFF reads audit record → timeline populates

Currently the agent's audit node is a stub (logs only). Once wired to Kafka, the BFF lights up automatically.

---

## Deployment

```bash
make build-chatbot-image
make push-all-images REGISTRY=quay.io/rh-ee-mtalvi
make helm-install REGISTRY=quay.io/rh-ee-mtalvi
```

---

## Tests

| Type | Count | Result | Command |
|------|-------|--------|---------|
| Unit | 29 | All passed | `cd hub/chatbot-service && uv run pytest tests/ -v -o "addopts="` |
| Integration (chatbot) | 6 | All passed | `make integration-tests` |
| Integration (full suite) | 58 | 42 passed, 3 failed, 13 skipped | `make integration-tests` |

**Integration test details (Jun 18, 2026):**
- Chatbot BFF: 6/6 passed (health, summary, integrations, chat, chat empty, demo trigger)
- Agent service: 5/5 passed
- Ingestion pipeline: 8/8 passed (2 skipped)
- MCP servers: 23/23 passed (11 skipped — LokiStack disabled)
- OpenShift MCP: 3 failed — RBAC issue (ClusterRoleBinding points to wrong namespace). Will be addressed in a separate PR.
