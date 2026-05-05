# Langfuse Helm Deployment

## Quick Start

```bash
make langfuse-install      # full deploy (repo, namespace, secrets, helm install, wait)
make langfuse-status       # check pod/service health
make langfuse-port-forward # access UI at http://localhost:3000
make langfuse-uninstall    # full teardown
make help                  # list all targets
```

## Prerequisites

- Kubernetes cluster access
- `helm` and `kubectl` installed
- Langfuse Helm repo added:
  ```bash
  helm repo add langfuse https://langfuse.github.io/langfuse-k8s
  helm repo update
  ```

## Deploy

1. Create the namespace:
   ```bash
   kubectl create namespace tgolan-langfuse
   ```

2. Generate and create the secrets:
   ```bash
   chmod +x hub/infra/langfuse/create-secrets.sh
   ./hub/infra/langfuse/create-secrets.sh tgolan-langfuse
   ```

3. Install the chart:
   ```bash
   helm install langfuse langfuse/langfuse \
     -n tgolan-langfuse \
     -f hub/infra/langfuse/values.yaml \
     --version 1.5.22
   ```

4. Wait for pods (~60s for migrations):
   ```bash
   kubectl get pods -n tgolan-langfuse -w
   ```

5. Access the UI:
   ```bash
   kubectl port-forward svc/langfuse-web -n tgolan-langfuse 3000:3000
   # open http://localhost:3000
   ```

## Upgrade

```bash
helm repo update
helm upgrade langfuse langfuse/langfuse \
  -n tgolan-langfuse \
  -f hub/infra/langfuse/values.yaml \
  --version 1.5.22
```

## Teardown

```bash
helm uninstall langfuse -n tgolan-langfuse
kubectl delete pvc --all -n tgolan-langfuse
kubectl delete namespace tgolan-langfuse
```
