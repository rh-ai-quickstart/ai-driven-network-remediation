# Langfuse Deployment Guide

## Overview

[Langfuse](https://langfuse.com) provides LLM observability and tracing for the
AI-driven network remediation agent. It captures every LLM call, token usage,
latency, and session context so you can debug, evaluate, and improve agentic
workflows.

Langfuse is deployed alongside the main application via the upstream
[Langfuse Helm chart](https://github.com/langfuse/langfuse-k8s) and is gated
behind the `ENABLE_LANGFUSE` flag.

## Quick Start

```bash
# Deploy with Langfuse
ENABLE_LANGFUSE=true make helm-install

# Access the UI
make langfuse-port-forward   # http://localhost:3000

# Login: admin@local.dev / changeme
```

## What Gets Deployed

All components deploy into the same namespace as the main app (default: `hub`):

| Component   | Description                          |
|-------------|--------------------------------------|
| langfuse-web    | Langfuse web UI and API          |
| langfuse-worker | Background worker (async tasks)  |
| PostgreSQL      | Langfuse metadata store          |
| ClickHouse      | Trace/event analytics (single-node) |
| Redis           | Queue and caching                |
| MinIO           | S3-compatible blob storage       |

## Secrets

A single Kubernetes secret (`langfuse-secrets`) holds all credentials. It is
created automatically by `create-secrets.sh` during install.

The script uses a **merge strategy**: existing keys are preserved, only missing
keys are generated. This makes `make helm-install` safe to re-run without
rotating credentials or breaking API key references.

**Prerequisites:** `oc`, `jq`, and `openssl` must be available.

To inspect the generated API keys:

```bash
oc get secret langfuse-secrets -n hub -o jsonpath='{.data.langfuse-public-key}' | base64 -d
oc get secret langfuse-secrets -n hub -o jsonpath='{.data.langfuse-secret-key}' | base64 -d
```

## Headless Initialization

On first boot, Langfuse auto-creates:

- **Organization:** AI Driven Network Remediation
- **Project:** default
- **Admin user:** admin@local.dev / changeme
- **API keys:** pre-seeded from `langfuse-secrets` (stable across restarts)

No manual UI setup is required.

## Day-2 Operations

```bash
make langfuse-status         # pods, services, secrets
make langfuse-port-forward   # UI at http://localhost:3000
make langfuse-upgrade        # upgrade to pinned chart version
```

Override the namespace: `NAMESPACE=my-ns make langfuse-status`

## Uninstall

```bash
ENABLE_LANGFUSE=true make helm-uninstall
```

This removes the Langfuse Helm release, its PVCs, and the `langfuse-secrets`
secret. The main application is also uninstalled.

## SDK Integration (Future PR)

The agent service will integrate Langfuse tracing via the
[Langfuse LangChain callback handler](https://langfuse.com/docs/integrations/langchain/tracing).
The pattern (implemented in [it-self-service-agent](https://github.com/rh-ai-quickstart/it-self-service-agent)):

1. Add `langfuse` to the agent service dependencies
2. Inject env vars into agent pods:
   - `LANGFUSE_ENABLED=true`
   - `LANGFUSE_PUBLIC_KEY` (from `langfuse-secrets`)
   - `LANGFUSE_SECRET_KEY` (from `langfuse-secrets`)
   - `LANGFUSE_HOST=http://langfuse-web:3000`
3. Wire the callback handler into LangGraph:

```python
from langfuse.langchain import CallbackHandler

handler = CallbackHandler()  # reads LANGFUSE_* env vars
config = {"callbacks": [handler]}
graph.invoke(input, config=config)
```

The secrets and Langfuse instance are already provisioned by the infra layer;
only the application-side wiring remains.
