#!/bin/sh
set -o errexit

# Mirror the Network nightly e2e flow with custom images built from the
# local codebase.
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
REGISTRY="${REGISTRY}" VERSION="${VERSION}" ENABLE_TELCO_ORAN=false ENABLE_NETWORK_REMEDIATION=true make build-all-images

echo "Pushing images"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" ENABLE_TELCO_ORAN=false ENABLE_NETWORK_REMEDIATION=true make push-all-images

echo "Deploying (Network only)"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" NAMESPACE="${NAMESPACE}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" \
	ENABLE_TELCO_ORAN=false \
	ENABLE_NETWORK_REMEDIATION=true \
	AUTO_INGEST_ON_STARTUP=false make helm-install

echo "Creating edge workload in namespace ${EDGE_NAMESPACE}"
EDGE_NAMESPACE="${EDGE_NAMESPACE}" make deploy-edge-workload

echo "Running Network integration tests"
NAMESPACE="${NAMESPACE}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" make network-integration-tests
