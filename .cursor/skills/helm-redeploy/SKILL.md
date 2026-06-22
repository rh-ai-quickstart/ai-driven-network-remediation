# Helm Redeploy (OpenShift)

Build/push images (if needed), tear down the quickstart Helm releases in **`ai-driven-network-remediation-itay`**, then redeploy.

**Always use `oc` (never `kubectl`).** **Always use namespace `ai-driven-network-remediation-itay`.**

Run from the repository root.

## Quick start (preferred)

By default **`SKIP_UNINSTALL=false`**: the script always runs `make helm-uninstall` before `make helm-install`.

```bash
.cursor/skills/helm-redeploy/scripts/redeploy.sh
```

The script:
1. Checks `oc` login and `.env` (`ADNR_LLM_*`)
2. Sets `oc project ai-driven-network-remediation-itay`
3. Ensures Quay pull secret `quay-ikatav-pull`
4. **Builds and pushes images when needed** (see below)
5. Runs `make helm-uninstall` then `make helm-install`
6. **On install failure**, retries with fallbacks:
   - Mirror public images from `quay.io/rh-ai-quickstart` → `REGISTRY` via `skopeo`
   - Force-build and push all hub/MCP/mock images
   - Patch mock deployments with `imagePullSecrets`
   - Delete stale `pg-data-pgvector-0` PVC
   - Retry `make helm-install`
7. Verifies all pods and helm releases (`hub`, `kafka`, `minio`)

### Image build behavior

When `REGISTRY` is your personal registry (default `quay.io/ikatav`):

| Condition | Action |
|-----------|--------|
| Any required image missing from registry | `make build-all-images`, `push-all-images`, mock build/push |
| All images already in registry | Skip build (log message) |
| `FORCE_BUILD=true` | Always rebuild and push (use after local code changes with the same tag) |
| `SKIP_BUILD=true` | Never build (redeploy existing registry images only) |

When `REGISTRY=quay.io/rh-ai-quickstart`, local build is skipped; the install-failure fallback mirrors upstream images instead.

Images built: `noc-chatbot-service`, `noc-ingestion-pipeline`, `noc-agent-service`, all `noc-mcp-*` servers, `aap-mock`, `servicenow-mock`.

### Script options

| Variable | Default | Purpose |
|----------|---------|---------|
| `REGISTRY` | `quay.io/ikatav` | Container image registry |
| `VERSION` | `0.1.0` | Image tag |
| `CONTAINER_TOOL` | `podman` | Container CLI for build/push |
| `SKIP_UNINSTALL` | **`false`** | Full redeploy: uninstall then install. Set `true` only to retry install after a partial failure |
| `SKIP_BUILD` | **`false`** | Skip build/push; use images already in registry |
| `FORCE_BUILD` | **`false`** | Rebuild all images even if they exist in registry (same tag, new code) |

```bash
# Default: build if needed, uninstall, install
REGISTRY=quay.io/ikatav VERSION=0.1.0 .cursor/skills/helm-redeploy/scripts/redeploy.sh

# After code changes (same tag)
FORCE_BUILD=true .cursor/skills/helm-redeploy/scripts/redeploy.sh

# Redeploy only — no rebuild
SKIP_BUILD=true .cursor/skills/helm-redeploy/scripts/redeploy.sh

# Install-only retry (skip uninstall)
SKIP_UNINSTALL=true .cursor/skills/helm-redeploy/scripts/redeploy.sh
```

Pass optional Make flags via environment (applied to both uninstall and install if you run make manually):

```bash
ENABLE_LANGFUSE=true ENABLE_LOKISTACK=true .cursor/skills/helm-redeploy/scripts/redeploy.sh
```

## Manual fallback

If the script is unavailable, run:

```bash
NS=ai-driven-network-remediation-itay
oc project ai-driven-network-remediation-itay
set -a && source .env && set +a
export REGISTRY=quay.io/ikatav VERSION=0.1.0

REGISTRY=$REGISTRY VERSION=$VERSION make build-all-images push-all-images
REGISTRY=$REGISTRY VERSION=$VERSION make build-push-aap-mock build-push-servicenow-mock

NAMESPACE=$NS EDGE_NAMESPACE=$NS make helm-uninstall
NAMESPACE=$NS EDGE_NAMESPACE=$NS make helm-install
```

See [reference.md](reference.md) for image-pull and PVC troubleshooting.

## Verify

```bash
oc project ai-driven-network-remediation-itay
helm list -n ai-driven-network-remediation-itay
oc get pods -n ai-driven-network-remediation-itay
oc get routes -n ai-driven-network-remediation-itay
```

All pods should be `Running` (jobs `Completed`). Helm release: `hub` (kafka, minio, and mocks are subcharts/templates within the hub release).

## After redeploy

```bash
set -a && source .env && set +a
NAMESPACE=ai-driven-network-remediation-itay EDGE_NAMESPACE=ai-driven-network-remediation-itay make integration-tests
```
