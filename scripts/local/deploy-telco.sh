#!/bin/sh
set -o errexit

# Mirror the Telco ORAN nightly e2e flow (nightly-e2e-telco-oran-ocp.yml)
# with custom images built from the local codebase.
# Assumes you are already logged in to both OpenShift and Quay.
REGISTRY="${REGISTRY:-quay.io/rh-ai-quickstart}"
VERSION="${VERSION:-0.1.0}"
NAMESPACE="${NAMESPACE:-hub}"
EDGE_NAMESPACE="${EDGE_NAMESPACE:-$NAMESPACE}"

echo "Using REGISTRY=${REGISTRY}"
echo "Using VERSION=${VERSION}"
echo "Using NAMESPACE=${NAMESPACE}"
echo "Using EDGE_NAMESPACE=${EDGE_NAMESPACE}"

echo "Cleaning up existing deployment"
NAMESPACE="${NAMESPACE}" make helm-uninstall

echo "Building images"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" ENABLE_TELCO_ORAN=true ENABLE_NETWORK_REMEDIATION=false make build-all-images

echo "Pushing images"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" ENABLE_TELCO_ORAN=true ENABLE_NETWORK_REMEDIATION=false make push-all-images

echo "Deploying (Telco ORAN only)"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" NAMESPACE="${NAMESPACE}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" \
	ENABLE_TELCO_ORAN=true \
	ENABLE_NETWORK_REMEDIATION=false \
	AUTO_INGEST_ON_STARTUP=false make helm-install

echo "Running Telco ORAN integration tests"
NAMESPACE="${NAMESPACE}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" make telco-integration-tests
