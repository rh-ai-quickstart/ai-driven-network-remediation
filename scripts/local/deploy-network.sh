#!/bin/sh
set -o errexit

# Mirror the Network nightly e2e flow with custom images built from the
# local codebase.
# Assumes you are already logged in to both OpenShift and Quay.
REGISTRY="${REGISTRY:-quay.io/rh-ai-quickstart}"
VERSION="${VERSION:-0.1.0}"
NAMESPACE="${NAMESPACE:-hub}"
EDGE_NAMESPACE="${EDGE_NAMESPACE:-dark-noc-edge}"

echo "Using REGISTRY=${REGISTRY}"
echo "Using VERSION=${VERSION}"
echo "Using NAMESPACE=${NAMESPACE}"
echo "Using EDGE_NAMESPACE=${EDGE_NAMESPACE}"

echo "Cleaning up existing deployment"
CLUSTER_COUNT=1 NAMESPACE="${NAMESPACE}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" make teardown

echo "Building images"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" ENABLE_TELCO_ORAN=false ENABLE_NETWORK_REMEDIATION=true make build-all-images

echo "Pushing images"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" ENABLE_TELCO_ORAN=false ENABLE_NETWORK_REMEDIATION=true make push-all-images

echo "Deploying (Network only)"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" NAMESPACE="${NAMESPACE}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" \
	ENABLE_TELCO_ORAN=false \
	ENABLE_NETWORK_REMEDIATION=true \
	AUTO_INGEST_ON_STARTUP=false make helm-install

echo "Installing edge chart in namespace ${EDGE_NAMESPACE}"
CLUSTER_COUNT=1 EDGE_GITOPS=helm \
	REGISTRY="${REGISTRY}" VERSION="${VERSION}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" \
	CLUSTER_LOG_FORWARDER_ENABLED=false make edge-deploy

echo "Running Network integration tests"
NAMESPACE="${NAMESPACE}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" make network-integration-tests
