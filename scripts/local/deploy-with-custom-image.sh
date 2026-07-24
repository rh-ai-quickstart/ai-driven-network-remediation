#!/bin/sh
set -o errexit

# Mirror the "Build (custom image from the codebase)" flow from docs/manual-deploy.md.
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
REGISTRY="${REGISTRY}" VERSION="${VERSION}" make build-all-images

echo "Building AAP mock image"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" make build-push-aap-mock

echo "Building ServiceNow mock image"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" make build-push-servicenow-mock

echo "Pushing images"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" make push-all-images

echo "Deploying"
# Avoid racing the startup auto-ingest against the integration tests' own explicit sync/ingest
# calls (see CI's kind action for the same override).
REGISTRY="${REGISTRY}" VERSION="${VERSION}" NAMESPACE="${NAMESPACE}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" \
	AUTO_INGEST_ON_STARTUP=false make helm-install

echo "Creating edge workload in namespace ${EDGE_NAMESPACE}"
EDGE_NAMESPACE="${EDGE_NAMESPACE}" make deploy-edge-workload

echo "Running integration tests"
NAMESPACE="${NAMESPACE}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" make integration-tests

echo "Running unit tests"
NAMESPACE="${NAMESPACE}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" make unit-tests
