#!/bin/sh
set -o errexit

# Mirror the "Build (custom image from the codebase)" flow from docs/manual-deploy.md.
# Assumes you are already logged in to both OpenShift and Quay.

echo "Cleaning up existing deployment"
make teardown

echo "Building images"
make build-all-images

echo "Building AAP mock image"
make build-push-aap-mock

echo "Building ServiceNow mock image"
make build-push-servicenow-mock

echo "Pushing images"
make push-all-images

echo "Deploying"
# Avoid racing the startup auto-ingest against the integration tests' own explicit sync/ingest
# calls (see CI's kind action for the same override).
AUTO_INGEST_ON_STARTUP=false make helm-install

echo "Installing edge chart"
EDGE_GITOPS=helm make edge-deploy

echo "Running unit tests"
make unit-tests
make multi-cluster-template-tests

echo "Running integration tests"
make integration-tests
