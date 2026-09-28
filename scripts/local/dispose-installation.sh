#!/bin/sh
set -o errexit

# Mirror the "Undeploy the hub" flow from docs/manual-deploy.md.
# Tears down single-cluster edge GitOps/Helm ownership then the hub chart.
# Assumes you are already logged in to OpenShift.
NAMESPACE="${NAMESPACE:-hub}"
EDGE_NAMESPACE="${EDGE_NAMESPACE:-dark-noc-edge}"
CLUSTER_COUNT="${CLUSTER_COUNT:-1}"

echo "Using NAMESPACE=${NAMESPACE}"
echo "Using EDGE_NAMESPACE=${EDGE_NAMESPACE}"
echo "Using CLUSTER_COUNT=${CLUSTER_COUNT}"

echo "Cleaning up deployment (edge + hub)"
CLUSTER_COUNT="${CLUSTER_COUNT}" NAMESPACE="${NAMESPACE}" EDGE_NAMESPACE="${EDGE_NAMESPACE}" make acm-teardown
