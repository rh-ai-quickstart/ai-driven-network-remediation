#!/bin/sh
set -o errexit

KSERVE_VERSION="${KSERVE_VERSION:-v0.17.0}"
CERT_MANAGER_VERSION="${CERT_MANAGER_VERSION:-v1.17.0}"

echo "==> Installing cert-manager ${CERT_MANAGER_VERSION}"
oc apply -f "https://github.com/cert-manager/cert-manager/releases/download/${CERT_MANAGER_VERSION}/cert-manager.yaml"
oc wait --for=condition=Available deployment/cert-manager -n cert-manager --timeout=120s
oc wait --for=condition=Available deployment/cert-manager-webhook -n cert-manager --timeout=120s

echo "==> Installing KServe CRDs ${KSERVE_VERSION}"
helm install kserve-crd oci://ghcr.io/kserve/charts/kserve-crd \
  --version "${KSERVE_VERSION}" \
  --namespace kserve --create-namespace

echo "==> Installing KServe controller (RawDeployment mode) ${KSERVE_VERSION}"
helm install kserve oci://ghcr.io/kserve/charts/kserve-resources \
  --version "${KSERVE_VERSION}" \
  --namespace kserve \
  --set kserve.controller.deploymentMode=RawDeployment
oc wait --for=condition=Available deployment/kserve-controller-manager -n kserve --timeout=120s

echo "==> Installing ODH CRDs (DataScienceCluster, DSCInitialization)"
ODH_CRD_BASE="https://raw.githubusercontent.com/redhat-openshift-ecosystem/community-operators-prod/main/operators/opendatahub-operator/3.4.0-ea.2/manifests"
oc apply --server-side -f "${ODH_CRD_BASE}/datasciencecluster.opendatahub.io_datascienceclusters.yaml"
oc apply --server-side -f "${ODH_CRD_BASE}/dscinitialization.opendatahub.io_dscinitializations.yaml"

echo "==> Installing LlamaStackDistribution CRD (for AutoRAG)"
oc apply --server-side -f "https://raw.githubusercontent.com/llamastack/llama-stack-k8s-operator/main/config/crd/bases/llamastack.io_llamastackdistributions.yaml"

echo "==> OpenShift AI components installed successfully"
oc get crd | grep -E "kserve|opendatahub|llamastack" || true
