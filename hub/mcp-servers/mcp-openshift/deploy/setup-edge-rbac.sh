#!/usr/bin/env bash
set -euo pipefail

EDGE_NAMESPACE="${1:?Usage: $0 <edge-namespace> <hub-namespace> [api-server-url]}"
HUB_NAMESPACE="${2:?Usage: $0 <edge-namespace> <hub-namespace> [api-server-url]}"
EDGE_SERVER="${3:-https://kubernetes.default.svc}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SA_NAME=noc-openshift-mcp

oc create namespace "$EDGE_NAMESPACE" 2>/dev/null || true
sed "s/EDGE_NAMESPACE_PLACEHOLDER/$EDGE_NAMESPACE/g" "$SCRIPT_DIR/edge-rbac.yaml" \
    | oc apply -n "$EDGE_NAMESPACE" -f -

for _ in 1 2 3 4 5; do
    TOKEN=$(oc get secret "$SA_NAME-token" -n "$EDGE_NAMESPACE" \
        -o jsonpath='{.data.token}' 2>/dev/null || true)
    [ -n "$TOKEN" ] && break
    sleep 2
done
[ -z "$TOKEN" ] && echo "ERROR: token not populated after waiting" && exit 1
TOKEN=$(echo "$TOKEN" | base64 -d)

CA_FILE=$(mktemp)
oc get secret "$SA_NAME-token" -n "$EDGE_NAMESPACE" \
    -o jsonpath='{.data.ca\.crt}' | base64 -d > "$CA_FILE"

EDGE_KC=$(mktemp)
KUBECONFIG="$EDGE_KC" oc config set-cluster edge \
    --server="$EDGE_SERVER" --certificate-authority="$CA_FILE" --embed-certs=true > /dev/null
KUBECONFIG="$EDGE_KC" oc config set-credentials "$SA_NAME" --token="$TOKEN" > /dev/null
KUBECONFIG="$EDGE_KC" oc config set-context edge \
    --cluster=edge --namespace="$EDGE_NAMESPACE" --user="$SA_NAME" > /dev/null
KUBECONFIG="$EDGE_KC" oc config use-context edge > /dev/null

oc create secret generic noc-openshift-edge-kubeconfig \
    --from-file=kubeconfig="$EDGE_KC" \
    -n "$HUB_NAMESPACE" --dry-run=client -o yaml | oc apply -f -

rm -f "$CA_FILE" "$EDGE_KC"
