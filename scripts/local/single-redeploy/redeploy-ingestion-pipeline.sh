#!/bin/sh
set -o errexit

REGISTRY="${REGISTRY:-quay.io/rh-ai-quickstart}"
VERSION="${VERSION:-0.1.0}"
NAMESPACE="${NAMESPACE:-hub}"

echo "Building ingestion-pipeline image"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" make build-ingestion-image

echo "Building ran-chatbot-service image"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" make build-ran-chatbot-image

echo "Pushing images"
CONTAINER_TOOL="${CONTAINER_TOOL:-podman}"
$CONTAINER_TOOL push "${REGISTRY}/noc-ingestion-pipeline:${VERSION}"
$CONTAINER_TOOL push "${REGISTRY}/noc-ran-chatbot-service:${VERSION}"

echo "Restarting llamastack deployment"
oc rollout restart deployment/llamastack -n "${NAMESPACE}"
oc rollout status deployment/llamastack -n "${NAMESPACE}" --timeout=300s

echo "Restarting ingestion-pipeline deployment (auto-ingest disabled)"
oc set env deployment/hub-ingestion-pipeline AUTO_INGEST_ON_STARTUP=false -n "${NAMESPACE}"
oc rollout restart deployment/hub-ingestion-pipeline -n "${NAMESPACE}"
oc rollout status deployment/hub-ingestion-pipeline -n "${NAMESPACE}" --timeout=120s

echo "Restarting ran-chatbot-service deployment"
oc rollout restart deployment/hub-ran-chatbot-service -n "${NAMESPACE}"
oc rollout status deployment/hub-ran-chatbot-service -n "${NAMESPACE}" --timeout=120s

echo "Running telco docs integration test"
oc port-forward -n "${NAMESPACE}" svc/hub-ingestion-pipeline 8000:8000 &
PF_INGESTION_PID=$!
oc port-forward -n "${NAMESPACE}" svc/llamastack-service 8321:8321 &
PF_LLAMASTACK_PID=$!
trap "kill $PF_INGESTION_PID $PF_LLAMASTACK_PID" EXIT
sleep 2 && cd hub/integration-tests && \
uv run pytest tests/telco/test_telco_docs.py::test_telco_docs_sync_ingest_and_content_flow -v
