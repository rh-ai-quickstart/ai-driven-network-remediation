#!/usr/bin/env bash
# Creates or updates the langfuse-secrets Kubernetes secret.
# Existing keys are preserved (merge strategy); missing keys are generated.
#
# Usage:
#   ./create-secrets.sh [NAMESPACE]
#   # default namespace: hub

set -euo pipefail

NAMESPACE="${1:-hub}"
SECRET_NAME="langfuse-secrets"

command -v jq >/dev/null 2>&1 || { echo "Error: jq is required but not installed." >&2; exit 1; }

EXISTING=""
if oc get secret "$SECRET_NAME" -n "$NAMESPACE" -o json >/dev/null 2>&1; then
  EXISTING=$(oc get secret "$SECRET_NAME" -n "$NAMESPACE" -o json)
fi

get_existing() {
  local key="$1"
  if [ -n "$EXISTING" ]; then
    local val
    val=$(echo "$EXISTING" | jq -r ".data[\"$key\"] // empty" | base64 -d 2>/dev/null) || true
    if [ -n "$val" ]; then
      echo "$val"
      return
    fi
  fi
  return 1
}

get_or_generate() {
  local key="$1" cmd="$2"
  get_existing "$key" 2>/dev/null && return
  eval "$cmd"
}

SALT=$(get_or_generate salt "openssl rand -base64 32")
NEXTAUTH_SECRET=$(get_or_generate nextauth-secret "openssl rand -base64 32")
ENCRYPTION_KEY=$(get_or_generate encryption-key "openssl rand -hex 32")

PG_PASS_EXISTING=$(get_existing postgres-password 2>/dev/null || true)
if [ -n "$PG_PASS_EXISTING" ]; then
  PG_PASSWORD="$PG_PASS_EXISTING"
else
  PG_PASSWORD="$(openssl rand -hex 16)"
fi
PG_PASSWORD_DUP=$(get_existing postgresql-password 2>/dev/null || echo "$PG_PASSWORD")

CLICKHOUSE_PASSWORD=$(get_or_generate clickhouse-password "openssl rand -hex 16")
REDIS_PASSWORD=$(get_or_generate redis-password "openssl rand -hex 16")
MINIO_ACCESS_KEY=$(get_or_generate minio-access-key "openssl rand -hex 12")
MINIO_SECRET_KEY=$(get_or_generate minio-secret-key "openssl rand -hex 24")
LANGFUSE_PUBLIC_KEY=$(get_or_generate langfuse-public-key "echo lf_pk_\$(openssl rand -hex 16)")
LANGFUSE_SECRET_KEY=$(get_or_generate langfuse-secret-key "echo lf_sk_\$(openssl rand -hex 24)")

oc create secret generic "$SECRET_NAME" \
  --namespace "$NAMESPACE" \
  --from-literal=salt="$SALT" \
  --from-literal=nextauth-secret="$NEXTAUTH_SECRET" \
  --from-literal=encryption-key="$ENCRYPTION_KEY" \
  --from-literal=postgres-password="$PG_PASSWORD" \
  --from-literal=postgresql-password="$PG_PASSWORD_DUP" \
  --from-literal=clickhouse-password="$CLICKHOUSE_PASSWORD" \
  --from-literal=redis-password="$REDIS_PASSWORD" \
  --from-literal=minio-access-key="$MINIO_ACCESS_KEY" \
  --from-literal=minio-secret-key="$MINIO_SECRET_KEY" \
  --from-literal=langfuse-public-key="$LANGFUSE_PUBLIC_KEY" \
  --from-literal=langfuse-secret-key="$LANGFUSE_SECRET_KEY" \
  --dry-run=client -o yaml | oc apply -f -

echo "Secret '$SECRET_NAME' created/updated in namespace '$NAMESPACE'"
