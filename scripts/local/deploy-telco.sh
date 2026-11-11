#!/bin/sh
set -o errexit

# Mirror the Telco ORAN nightly e2e flow (nightly-e2e-telco-oran-ocp.yml)
# with custom images built from the local codebase.
# Assumes you are already logged in to both OpenShift and Quay.

echo "Cleaning up existing deployment"
make helm-uninstall

echo "Building images"
ENABLE_TELCO_ORAN=true ENABLE_NETWORK_REMEDIATION=false make build-all-images

echo "Pushing images"
ENABLE_TELCO_ORAN=true ENABLE_NETWORK_REMEDIATION=false make push-all-images

echo "Deploying (Telco ORAN only)"
ENABLE_TELCO_ORAN=true \
	ENABLE_NETWORK_REMEDIATION=false \
AUTO_INGEST_ON_STARTUP=false make helm-install

echo "Running Telco ORAN integration tests"
make telco-integration-tests
