#!/bin/sh
set -o errexit

# Mirror the Network nightly e2e flow with custom images built from the
# local codebase.
# Assumes you are already logged in to both OpenShift and Quay.

echo "Cleaning up existing deployment"
ENABLE_NETWORK_REMEDIATION=true make teardown

echo "Building images"
ENABLE_TELCO_ORAN=false ENABLE_NETWORK_REMEDIATION=true make build-all-images

echo "Pushing images"
ENABLE_TELCO_ORAN=false ENABLE_NETWORK_REMEDIATION=true make push-all-images

echo "Deploying (Network only)"
ENABLE_TELCO_ORAN=false \
	ENABLE_NETWORK_REMEDIATION=true \
	AUTO_INGEST_ON_STARTUP=false make helm-install

echo "Installing edge chart"
EDGE_GITOPS=helm make edge-deploy

echo "Installing enabled CPU demo workloads"
ENABLE_NETWORK_REMEDIATION=true make cpu-demo-deploy-enabled

echo "Running Network integration tests"
make network-integration-tests
