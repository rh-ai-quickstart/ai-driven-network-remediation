# NOC Frontend — V1

React dashboard for the AI-Driven Network Remediation quickstart. Polls the chatbot BFF and displays real-time operational status.

## Quick Start

```bash
# Terminal 1: port-forward the BFF
oc port-forward -n hub svc/hub-chatbot-service 8080:80

# Terminal 2: run the dev server
cd hub/frontend
npm install
npm run dev
# Open http://localhost:5173
```

The Vite dev server proxies `/api/*` to `localhost:8080` automatically.

## Tech Stack

- React 19 + Vite 6
- Plain CSS (dark NOC theme)
- nginx (production container)
- No external UI framework

## Architecture

```
┌──────────────┐       /api/*        ┌─────────────────────┐
│   Browser    │ ───────────────────► │  nginx (frontend)   │
│  React SPA   │                      │  proxy to BFF       │
└──────────────┘                      └────────┬────────────┘
                                               │
                                               ▼
                                      ┌─────────────────────┐
                                      │  hub-chatbot-service │
                                      │  (FastAPI BFF)       │
                                      └─────────────────────┘
```

In development, Vite's built-in proxy replaces nginx.

## BFF Endpoints Consumed

| Endpoint | Method | Interval | What it drives |
|----------|--------|----------|----------------|
| `/api/summary` | GET | 10s poll | Header metrics, status cards |
| `/api/integrations` | GET | 10s poll | Integration matrix, SLO, business impact, incident timeline |
| `/api/chat` | POST | User action | Chat panel |
| `/api/demo/trigger` | POST | User action | Demo trigger buttons |

## Project Structure

```
hub/frontend/
├── package.json          # Dependencies (react, vite)
├── vite.config.js        # Dev server + API proxy
├── index.html            # SPA entry
├── Containerfile         # Multi-stage build (node → nginx)
├── nginx.conf.template   # Reverse proxy for /api/* (envsubst'd at container startup)
└── src/
    ├── main.jsx          # React root
    ├── App.jsx           # Layout orchestrator
    ├── styles.css        # Dark NOC theme
    ├── hooks/
    │   └── usePolling.js # 10s interval polling hook
    └── components/
        ├── DegradedBanner.jsx    # Amber banner for _deps.status: "degraded"
        ├── HeaderMetrics.jsx     # Hero bar with totals
        ├── StatusCards.jsx       # 6 status cards (dep-aware)
        ├── IntegrationMatrix.jsx # MCP/platform health grid
        ├── SloPanel.jsx          # SLO metrics
        ├── BusinessImpact.jsx    # Cost/time savings
        ├── IncidentTimeline.jsx  # Incident movie replay
        ├── DemoTrigger.jsx       # 4 demo scenario buttons
        └── ChatPanel.jsx         # NOC chat with structured replies (dep-aware)
```

## API Response Shapes (V1)

The BFF returns nested objects. Key differences from the POC:

```js
// Chat response
data.model.name        // not data.model_name
data.model.source      // not data.model_source
data.model.framework   // new field
data.context.open_incidents    // not data.open_incidents
data.context.integrations_up   // not data.integrations_up

// Summary
data.servicenow = { reachable: true }  // UI shows reachable / unreachable

// Demo trigger success
data.incident_id  // UUID for traceability

// Demo trigger failure
HTTP 502 + { status: "error", detail: "..." }  // not HTTP 200
```

### Dependency Status (`_deps`)

All BFF data endpoints now include a `_deps` field signaling whether the response contains complete or partial data:

```jsonc
// All deps healthy — data is complete
{ "_deps": { "status": "ok" }, "open_incidents": 3, ... }

// Some deps unavailable — data is partial/fallback
{ "_deps": { "status": "degraded", "unavailable": ["kafka", "servicenow"] }, "open_incidents": 0, ... }
```

The frontend handles this with a single universal check:

| Condition | UI Behavior |
|-----------|-------------|
| `_deps.status === "ok"` (or `_deps` absent) | Normal display — no visual change |
| `_deps.status === "degraded"` | Amber banner at page top listing unavailable deps; affected cards show "unavailable" instead of misleading zeros |

Per-endpoint degradation mapping:

| Endpoint | Degrades when | Frontend effect |
|----------|---------------|-----------------|
| `/api/summary` | ServiceNow unreachable | ServiceNow card → "unavailable", Open Incidents card → "unavailable" |
| `/api/integrations` | Kafka or any probe down | Amber banner appears |
| `/api/chat` | LLM unreachable (fallback reply) | Reply annotated with `[⚠ Partial — ... unavailable]` |
| `/api/demo/trigger` | Kafka down | HTTP 502 (existing error handling) |

## Build & Deploy

```bash
# Build container image
make build-frontend-image

# Push to registry
podman push quay.io/rh-ai-quickstart/noc-frontend:0.1.0

# Deploy with Helm (deploys all services including frontend)
make helm-install
```

The Helm chart creates a Deployment, Service, and OpenShift Route with TLS edge termination.

## Access control

By default, the Route is **unauthenticated** — nginx proxies `/api/*` straight to `hub-chatbot-service`
with no login of any kind, including the endpoints with real side effects (`POST /api/demo/trigger`
publishes to the live `system-alerts` Kafka topic). This is intentional for local/demo use: anyone
with the cluster can `make helm-install` and click the demo buttons with zero setup, matching the
"Current Demo" narrative above.

For a shared or persistent cluster where the Route hostname might leak or be guessed, set
`global.frontendAuth.enabled=true` (or `make helm-install FRONTEND_AUTH_ENABLED=true`) to put an
OpenShift `oauth-proxy` sidecar in front of both this frontend and `hub-ran-frontend` — the standard
OpenShift pattern for gating a Route behind a cluster login. When enabled:

- The Service starts routing to the sidecar (port `8888`) instead of nginx directly, **and** nginx
  itself switches from listening on `0.0.0.0:8080` to `127.0.0.1:8080` only (via an
  `NGINX_LISTEN_ADDRESS` env override into the templated `nginx.conf.template` — see the
  Containerfile). Without this, any in-cluster client could still hit the pod's IP on `8080`
  directly and skip OAuth entirely, even with the Service pointed elsewhere. Because httpGet
  liveness/readiness probes connect to the pod's routable IP rather than loopback, they're swapped
  for an `exec`-based `wget` probe run inside the container's own network namespace instead.
- A `ServiceAccount` per frontend is created, annotated with
  `serviceaccounts.openshift.io/oauth-redirectreference.primary` so the sidecar can self-register
  as an OAuth client for that frontend's own Route — no manual `OAuthClient` object needed.
- A visitor hitting the Route is redirected to the cluster's login page; after authenticating, every
  same-origin request the SPA makes (including the demo-trigger and chat calls) just carries the
  resulting session cookie automatically — no frontend or BFF code changes needed.
- `npm run dev` and `oc port-forward` workflows are unaffected either way — the sidecar only wraps
  the built container's Route, not local dev traffic.

This is off by default so it doesn't change behavior for existing installs or break the demo
recording flow. See [`docs/LIGHTSPEED-DEMO-SCRIPT.md`](../../docs/LIGHTSPEED-DEMO-SCRIPT.md) for
this workflow's demo script.

**Note:** defaulting this to off (and letting it be disabled at all) is a QuickStart/demo
convenience, not something a released product should ship — see the comment on
`global.frontendAuth` in `hub/helm/values.yaml`.

Independently of that toggle, `nginx.conf.template` always rate-limits `/api/*` (see the `limit_req_zone`
directives at the top of the file): a generous zone for the polled `GET` endpoints
(`/api/summary`, `/api/integrations`) and `POST /api/chat`, and a much stricter zone for
`POST /api/demo/trigger` specifically, since it's the one endpoint here with a real side effect
(publishing to Kafka) and is only ever click-driven, never polled.

These zones key on `$binary_remote_addr`, which is **not** a reliable per-browser identifier here,
so treat them as a coarse-grained blast-radius bound rather than true per-client throttling:
without the auth gate, nginx's only visible peer is the OpenShift Route's router, so in practice
the bucket is shared per router replica, not per external client. With the auth gate on, nginx's
only visible peer is the oauth-proxy sidecar on `127.0.0.1`, so it collapses further into a single
bucket shared by every authenticated user hitting that pod. A real per-client fix would need
`ngx_http_realip_module` trusting a specific upstream's `X-Forwarded-For`, but the outermost hop
(the OpenShift router) doesn't have a fixed, cluster-independent IP range this chart could safely
trust — trusting the wrong range would make the header spoofable and defeat the limit entirely.
This is accepted as-is since rate limiting here is defense-in-depth underneath the real access
control (the login gate itself, or the blast-radius bound in the default demo mode).

## Environment Variables

| Variable | Where | Purpose |
|----------|-------|---------|
| `VITE_CHATBOT_URL` | Dev only | Override BFF target (default: relative `/api/*`) |

In production, nginx handles the proxy — no env vars needed at runtime.

## Tested On

Verified end-to-end on OpenShift cluster `ai-dev04.kni.syseng.devcluster.openshift.com`:

- Image: `quay.io/rh-ee-mtalvi/noc-frontend:0.1.0`
- Route: `https://hub-frontend-hub.apps.ai-dev04.kni.syseng.devcluster.openshift.com`
- BFF proxy: nginx → `hub-chatbot-service.hub.svc.cluster.local:80`

All panels render with live data, polling refreshes every 10s, demo triggers return `incident_id`, and chat returns structured executive replies with model metadata.

## Current Demo (Frontend PR)

This PR delivers the **operator visibility surface**. The demo today proves platform readiness:

1. **Dashboard loads with live data** — MCP servers up/down, Kafka connected, mocks healthy
2. **Chat works with real LLM** — Granite synthesizes live MCP context into executive summaries
3. **Demo triggers publish to Kafka** — returns `incident_id` confirming the event pipeline works
4. **Graceful degradation** — `_deps` banner shows when dependencies are unavailable

**What it does NOT show:** The autonomous remediation loop. After a trigger, nothing happens automatically — the agent doesn't consume the event, investigate, or fix anything.

### Current demo narrative

> "This is the operator surface for our AI-driven network remediation quickstart. The dashboard polls live infrastructure — MCP tool servers, Kafka, ServiceNow. An operator can chat with the AI to get an executive summary powered by Granite. We can trigger simulated failures that publish to our Kafka event pipeline. The autonomous remediation loop — where the agent picks up events and acts on them — is the next phase of work."

---

## V1 Demo After Agent Work (Follow-up Frontend PR)

Once the agent service is wired (Kafka consumer + MCP client + LLM + RAG + audit), the demo becomes:

**Narrative: "Watch a pod crash and get fixed by AI — no human intervention."**

### The flow

```
Second 0:   User clicks "Trigger CrashLoop" (or a pod actually dies)
            Event hits Kafka topic "system-alerts"

Second 5:   Agent consumes the event
            → "Detected" appears in incident timeline

Second 10:  Agent calls MCP OpenShift → gets pod status, restart count, events
Second 12:  Agent calls MCP LokiStack → finds error patterns in logs
Second 15:  Agent queries RAG → retrieves nginx-oomkilled runbook

Second 18:  Agent sends everything to Granite → gets root cause analysis
            "nginx container exceeded memory limits, confidence: 0.92"

Second 20:  Agent decides: confidence > 0.8 → remediate (not escalate)
Second 22:  Agent calls MCP AAP → triggers "restart-nginx" playbook
Second 25:  Agent calls MCP Slack → sends notification
Second 28:  Agent calls MCP ServiceNow → creates incident ticket

Second 30:  Agent writes audit record to Kafka "incident-audit"

Second 35:  Dashboard polls → incident timeline shows:
            "CrashLoopBackOff → Auto-Remediated"
            with AAP job ID + ServiceNow ticket link

Second 40:  User asks chat: "What just happened?"
            LLM responds with full incident narrative
```

### What changes in the dashboard

| Panel | Today (this PR) | After agent V1 |
|-------|-----------------|----------------|
| Incident Timeline | Always empty | Shows detected → remediated events in real time |
| SLO Panel | All "n/a" | Real MTTD, MTTR, auto-remediation % |
| Business Impact | All zeros | Counts go up with each incident |
| Open Incidents card | Always 0 | Goes to 1 during processing, back to 0 after fix |
| Chat | Generic summary of current state | Can discuss specific incidents with full context |
| Demo Trigger | Returns incident_id, then nothing | Returns incident_id, then you *watch the system fix it* |

### Follow-up frontend PR

Once the agent is complete, a follow-up frontend PR will:

- Add real-time incident status indicators (processing spinner while agent works)
- Enhance the incident timeline with expandable details (RCA, MCP evidence, playbook used)
- Add links to AAP job IDs and ServiceNow ticket numbers
- Improve chat to query specific incidents by ID
- Potentially add a "workflow steps" view showing the agent graph progression

The current frontend is **ready** for all of this — the panels already render whatever the BFF returns. The follow-up PR is about polish and richer visualizations once real data flows through.
