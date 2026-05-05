# Langfuse Helm Deployment

## Quick Start

```bash
make langfuse-install      # full deploy (repo, namespace, secrets, helm install, wait)
make langfuse-status       # check pod/service health
make langfuse-port-forward # access UI at http://localhost:3000
make langfuse-upgrade      # upgrade to latest pinned version
make langfuse-uninstall    # full teardown
```

## Prerequisites

- Kubernetes cluster access
- `helm` and `kubectl` installed
