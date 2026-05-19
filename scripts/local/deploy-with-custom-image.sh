#!/bin/sh
set -o errexit

# Mirror the "Build (custom image from the codebase)" flow from docs/manual-deploy.md.
# Assumes you are already logged in to both OpenShift and Quay.
REGISTRY="${REGISTRY:-quay.io/rh-ai-quickstart}"
VERSION="${VERSION:-0.1.0}"
NAMESPACE="${NAMESPACE:-hub}"

echo "Using REGISTRY=${REGISTRY}"
echo "Using VERSION=${VERSION}"
echo "Using NAMESPACE=${NAMESPACE}"

echo "Building images"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" make build-all-images

echo "Pushing images"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" make push-all-images

echo "Cleaning up existing deployment"
NAMESPACE="${NAMESPACE}" make helm-uninstall

echo "Deploying hub"
REGISTRY="${REGISTRY}" VERSION="${VERSION}" NAMESPACE="${NAMESPACE}" make helm-install

echo "Running integration tests"
NAMESPACE="${NAMESPACE}" make integration-tests
