# RAN Frontend — Telco O-RAN Anomaly Dashboard

React webapp for the Telco/O-RAN ML anomaly detection use case. Polls `ran-chatbot-service` for
recently detected RAN anomalies (ML-detected via Mantis AD on TelecomTS 5G lab traces) and lets an
operator chat about them in natural language.

This is an independent, standalone webapp — a twin of [`hub/frontend`](../frontend/FRONTEND.md)
(the NOC dashboard) built the same way, against a different (thinner) BFF: separate codebase,
separate Helm Deployment/Service/Route, own `ranFrontend.enabled` toggle, no shared runtime code
path with `hub/frontend` or `hub/chatbot-service`.

## Quick Start

```bash
# Terminal 1: port-forward the RAN chatbot BFF
oc port-forward -n hub svc/hub-ran-chatbot-service 8008:8003

# Terminal 2: run the dev server
cd hub/ran-frontend
npm install
npm run dev
# Open http://localhost:5174
```

The Vite dev server proxies `/api/*` to `localhost:8008` automatically.

## Tech Stack

- React 19 + Vite 6
- Plain CSS (dark, telecom-purple theme)
- nginx (production container)
- No external UI framework, no router

## Architecture

```
┌──────────────┐       /api/*        ┌──────────────────────┐
│   Browser    │ ───────────────────► │  nginx (ran-frontend) │
│  React SPA   │                      │  proxy to BFF          │
└──────────────┘                      └────────┬───────────────┘
                                                │
                                                ▼
                                       ┌──────────────────────┐
                                       │ ran-chatbot-service   │
                                       │ (FastAPI BFF)         │
                                       └──────────────────────┘
```

In development, Vite's built-in proxy replaces nginx.

## BFF Endpoints Consumed

| Endpoint | Method | Interval | What it drives |
|----------|--------|----------|----------------|
| `/api/anomalies` | GET | 10s poll (4s for ~75s after a demo trigger; an immediate refetch after Clear) | Header metrics, anomaly list panel |
| `/api/anomalies` | DELETE | User action | "Clear" button on the anomaly list panel |
| `/api/chat` | POST | User action | Chat panel |
| `/api/demo/trigger` | POST | User action | Demo Mode panel |

`ran-chatbot-service` is deliberately thin — it has no `/api/summary` or `/api/integrations`
equivalent, so this webapp has no matching panels either. See
[`docs/telco-oran-anomaly-detection.md`](../../docs/telco-oran-anomaly-detection.md) for the full
picture of what feeds `ran-chatbot-service`, and
[`docs/RAN-DEMO-SCRIPT.md`](../../docs/RAN-DEMO-SCRIPT.md) for a full demo-recording walkthrough
of the Demo Mode panel.

### Dependency status (`_deps`)

Both BFF endpoints include a `_deps` field, same convention as `hub/frontend`:

```jsonc
// Kafka connected — anomaly data is live
{ "_deps": { "status": "ok" }, "count": 2, "anomalies": [...] }

// Kafka unreachable — buffer may be stale/empty
{ "_deps": { "status": "degraded", "unavailable": ["kafka"] }, "count": 0, "anomalies": [] }
```

`_deps.status === "degraded"` shows an amber banner at the top of the page, and the header's
"Kafka Feed" metric switches to "Unavailable". `/api/chat` can additionally report `llm`
unavailable if the model endpoint is unreachable — that's annotated inline on the affected chat
reply instead.

### Clearing the anomaly list

The "Clear" button on the anomaly panel calls `DELETE /api/anomalies`, which empties
`ran-chatbot-service`'s in-memory buffer immediately. This is a process-local reset, not a
database delete: it does **not** survive a `ran-chatbot-service` restart or Kafka reconnect, since
the buffer re-seeds itself from the last ~50 messages on `ran-anomalies-enriched` (7-day retention)
every time it (re)connects. Useful for getting a clean slate before a demo recording — see
[`docs/RAN-DEMO-SCRIPT.md`](../../docs/RAN-DEMO-SCRIPT.md).

## Project Structure

```
hub/ran-frontend/
├── package.json          # Dependencies (react, vite)
├── vite.config.js        # Dev server + API proxy
├── index.html            # SPA entry
├── Containerfile         # Multi-stage build (node → nginx)
├── nginx.conf.template   # Reverse proxy for /api/* -> hub-ran-chatbot-service (envsubst'd at startup)
└── src/
    ├── main.jsx          # React root
    ├── App.jsx           # Layout orchestrator
    ├── styles.css        # Dark, purple-accented theme
    ├── hooks/
    │   └── usePolling.js # Polls /api/anomalies; exposes speedUpPolling() + refetchNow()
    └── components/
        ├── ErrorBoundary.jsx  # Render-error fallback
        ├── DegradedBanner.jsx # Amber banner for _deps.status: "degraded"
        ├── HeaderMetrics.jsx  # Anomalies tracked, cells affected, Kafka status
        ├── DemoTrigger.jsx    # Demo Mode: inject a synthetic reading into the real pipeline
        ├── AnomalyTable.jsx   # Recent anomalies + "Clear" button (DELETE /api/anomalies)
        └── ChatPanel.jsx      # RAN chat, parses the reply's Summary/Root Cause/
                                #   Recommended Fix/Model Output sections
```

## Build & Deploy

```bash
# Build container image
make build-ran-frontend-image

# Push to registry
podman push quay.io/rh-ai-quickstart/noc-ran-frontend:0.1.5

# Deploy with Helm — part of the Telco/O-RAN use case, on by default
make helm-install
```

The Helm chart creates a Deployment, Service, and OpenShift Route with TLS edge termination,
gated behind `global.telcoOran.enabled` and `ranFrontend.enabled` (both default `true`, same as
`ranAnomalyDetector.enabled` and the other RAN services) so the whole Telco/O-RAN stack deploys
together. Disable the entire use case with `ENABLE_TELCO_ORAN=false` (Make) / `--set
global.telcoOran.enabled=false` (Helm), or toggle just this webapp via `--set
ranFrontend.enabled=false`.

## Access control

By default, the Route is **unauthenticated** — nginx proxies `/api/*` straight to
`hub-ran-chatbot-service` with no login of any kind, including the two endpoints with real side
effects: `POST /api/demo/trigger` (publishes to the live `ran-combined-metrics` Kafka topic) and
`DELETE /api/anomalies` (wipes the live anomaly buffer). This is intentional for local/demo use —
see [`docs/RAN-DEMO-SCRIPT.md`](../../docs/RAN-DEMO-SCRIPT.md).

For a shared or persistent cluster where the Route hostname might leak or be guessed, set
`global.frontendAuth.enabled=true` (or `make helm-install FRONTEND_AUTH_ENABLED=true`) to put an
OpenShift `oauth-proxy` sidecar in front of both this frontend and `hub/frontend` (the same toggle
protects both, so the two dashboards stay consistent) — the standard OpenShift pattern for gating a
Route behind a cluster login. When enabled, a visitor hitting the Route is redirected to the
cluster's login page; after authenticating, every same-origin request the SPA makes — including
the "Clear" button's `DELETE /api/anomalies` and the Demo Mode `POST /api/demo/trigger` — just
carries the resulting session cookie automatically, so neither button needs any code change.
`npm run dev` and `oc port-forward` workflows are unaffected either way. This is off by default so
it doesn't change behavior for existing installs or break the demo recording flow.

Enabling the gate also switches nginx from listening on `0.0.0.0:8080` to `127.0.0.1:8080` only
(an `NGINX_LISTEN_ADDRESS` env override into the templated `nginx.conf.template`) — otherwise any
in-cluster client could still hit the pod's IP on `8080` directly and skip OAuth entirely, even
with the Service pointed at the sidecar. Liveness/readiness probes switch to an `exec`-based
`wget` check for the same reason: httpGet probes connect to the pod's routable IP, not loopback,
so they can't reach a loopback-only nginx.

**Note:** defaulting this to off (and letting it be disabled at all) is a QuickStart/demo
convenience, not something a released product should ship — see the comment on
`global.frontendAuth` in `hub/helm/values.yaml`.

Independently of that toggle, `nginx.conf.template` always rate-limits `/api/*` (see the `limit_req_zone`
and `map` directives at the top of the file): a generous zone for the polled `GET /api/anomalies`
traffic, and a much stricter zone — shared between `POST /api/demo/trigger` and
`DELETE /api/anomalies` — since both are click-driven, never polled, and are the two endpoints with
real side effects. A `map` on `$request_method` keeps `GET /api/anomalies` out of the strict zone
even though it shares a path with `DELETE /api/anomalies`.

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
| `VITE_RAN_CHATBOT_URL` | Dev only | Override BFF target (default: relative `/api/*`) |

In production, nginx handles the proxy — no env vars needed at runtime.
