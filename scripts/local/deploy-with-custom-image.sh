#!/bin/sh
set -o errexit

# Mirror the "Build (custom image from the codebase)" flow from docs/manual-deploy.md.
# Assumes you are already logged in to both OpenShift and Quay.
REGISTRY="${REGISTRY:-quay.io/rh-ai-quickstart}"
VERSION="${VERSION:-0.1.0}"
NAMESPACE="${NAMESPACE:-hub}"
ENABLE_HUB="${ENABLE_HUB:-true}"
ENABLE_KAFKA="${ENABLE_KAFKA:-true}"

echo "Using REGISTRY=${REGISTRY}"
echo "Using VERSION=${VERSION}"
echo "Using NAMESPACE=${NAMESPACE}"
echo "Using ENABLE_HUB=${ENABLE_HUB}"
echo "Using ENABLE_KAFKA=${ENABLE_KAFKA}"

if [ "${ENABLE_HUB}" = "true" ]; then
  echo "Building images"
  REGISTRY="${REGISTRY}" VERSION="${VERSION}" make build-all-images

  echo "Pushing images"
  REGISTRY="${REGISTRY}" VERSION="${VERSION}" make push-all-images

  echo "Cleaning up existing deployment"
  NAMESPACE="${NAMESPACE}" ENABLE_HUB="${ENABLE_HUB}" make helm-uninstall
fi

echo "Deploying"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" NAMESPACE="${NAMESPACE}" \
  ENABLE_HUB="${ENABLE_HUB}" ENABLE_KAFKA="${ENABLE_KAFKA}" make helm-install

echo "Running integration tests"
NAMESPACE="${NAMESPACE}" ENABLE_HUB="${ENABLE_HUB}" make integration-tests
