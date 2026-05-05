#!/usr/bin/env bash
# Creates the langfuse-secrets Kubernetes secret with generated values.
# Run once before `helm install`. Re-run only to rotate secrets.
#
# Usage:
#   ./create-secrets.sh [NAMESPACE]
#   # default namespace: langfuse

set -euo pipefail

NAMESPACE="${1:-langfuse}"

oc create secret generic langfuse-secrets \
  --namespace "$NAMESPACE" \
  --from-literal=salt="$(openssl rand -base64 32)" \
  --from-literal=nextauth-secret="$(openssl rand -base64 32)" \
  --from-literal=encryption-key="$(openssl rand -hex 32)" \
  --from-literal=postgresql-password="$(openssl rand -hex 16)" \
  --from-literal=clickhouse-password="$(openssl rand -hex 16)" \
  --from-literal=redis-password="$(openssl rand -hex 16)" \
  --dry-run=client -o yaml | oc apply -f -

echo "Secret 'langfuse-secrets' created/updated in namespace '$NAMESPACE'"
