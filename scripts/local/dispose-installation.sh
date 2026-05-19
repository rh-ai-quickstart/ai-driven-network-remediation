#!/bin/sh
set -o errexit

# Mirror the "Undeploy the hub" flow from docs/manual-deploy.md.
# Assumes you are already logged in to OpenShift.
NAMESPACE="${NAMESPACE:-hub}"

echo "Using NAMESPACE=${NAMESPACE}"

echo "Cleaning up deployment"
NAMESPACE="${NAMESPACE}" make helm-uninstall
