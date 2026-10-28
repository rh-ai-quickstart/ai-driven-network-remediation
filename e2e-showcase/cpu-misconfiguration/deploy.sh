#!/usr/bin/env bash
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART="$HERE/chart"
ACTION="${1:-deploy}"
REGISTRY="${REGISTRY:-}"
VERSION="${VERSION:-0.1.0}"
CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
EDGE_NAMESPACE="${EDGE_NAMESPACE:-dark-noc-edge}"
SITE_A="${SITE_A:-edge-site-01}"
SITE_B="${SITE_B:-edge-site-02}"
RELEASE="${RELEASE:-cpu-misconfiguration}"
FAULT_CPU_REQUEST="20m"
FAULT_CPU_LIMIT="25m"
KUBECONFIG_A=""
KUBECONFIG_B=""

cleanup() {
  [[ -z "$KUBECONFIG_A" ]] || rm -f "$KUBECONFIG_A"
  [[ -z "$KUBECONFIG_B" ]] || rm -f "$KUBECONFIG_B"
}

fetch_site_kubeconfig() {
  local site="$1" destination="$2" secret
  for secret in "$site-admin-kubeconfig" "$site-kubeconfig"; do
    if oc get secret "$secret" -n "$site" -o jsonpath='{.data.kubeconfig}' 2>/dev/null \
      | base64 -d 2>/dev/null >"$destination" && [[ -s "$destination" ]]; then
      return 0
    fi
    : >"$destination"
  done
  echo "Could not read spoke kubeconfig for '$site' (tried $site-admin-kubeconfig and $site-kubeconfig)" >&2
  return 1
}

fetch_kubeconfigs() {
  KUBECONFIG_A="$(mktemp)"
  trap cleanup EXIT
  KUBECONFIG_B="$(mktemp)"
  chmod 600 "$KUBECONFIG_A" "$KUBECONFIG_B"
  fetch_site_kubeconfig "$SITE_A" "$KUBECONFIG_A"
  fetch_site_kubeconfig "$SITE_B" "$KUBECONFIG_B"
}

deploy_single_cluster() {
  helm upgrade --install "$RELEASE" "$CHART" --create-namespace --namespace "$EDGE_NAMESPACE" \
    --set-string "siteId=$SITE_A" \
    --set-string "showcaseImage.registry=$REGISTRY" \
    --set-string "showcaseImage.tag=$VERSION" \
    --set-string "microserviceA.env.microserviceBSiteId=$SITE_A" \
    --wait --timeout 5m
}

undeploy_single_cluster() {
  helm uninstall "$RELEASE" --namespace "$EDGE_NAMESPACE" --ignore-not-found
}

deploy_multi_cluster() {
  helm upgrade --install "$RELEASE" "$CHART" --kubeconfig "$KUBECONFIG_B" \
    --create-namespace \
    --namespace "$EDGE_NAMESPACE" --set-string "siteId=$SITE_B" \
    --set-string "showcaseImage.registry=$REGISTRY" \
    --set-string "showcaseImage.tag=$VERSION" \
    --set client.enabled=false --set microserviceA.enabled=false \
    --set microserviceB.enabled=true --set microserviceB.route.enabled=true \
    --wait --timeout 5m
  local route_host
  route_host="$(oc --kubeconfig "$KUBECONFIG_B" get route microservice-b -n "$EDGE_NAMESPACE" -o jsonpath='{.spec.host}')"
  [[ -n "$route_host" ]] || { echo "No host assigned to the microservice-b Route" >&2; return 1; }

  helm upgrade --install "$RELEASE" "$CHART" --kubeconfig "$KUBECONFIG_A" \
    --create-namespace \
    --namespace "$EDGE_NAMESPACE" --set-string "siteId=$SITE_A" \
    --set-string "showcaseImage.registry=$REGISTRY" \
    --set-string "showcaseImage.tag=$VERSION" \
    --set client.enabled=true --set microserviceA.enabled=true \
    --set microserviceB.enabled=false \
    --set-string "microserviceA.env.microserviceBUrl=https://$route_host" \
    --set-string "microserviceA.env.microserviceBSiteId=$SITE_B" \
    --wait --timeout 5m
}

undeploy_multi_cluster() {
  helm uninstall "$RELEASE" --kubeconfig "$KUBECONFIG_B" --namespace "$EDGE_NAMESPACE" --ignore-not-found
  helm uninstall "$RELEASE" --kubeconfig "$KUBECONFIG_A" --namespace "$EDGE_NAMESPACE" --ignore-not-found
}

reset_demo() {
  local -a context_args=()
  if (( CLUSTER_COUNT >= 2 )); then
    KUBECONFIG_B="$(mktemp)"
    trap cleanup EXIT
    fetch_site_kubeconfig "$SITE_B" "$KUBECONFIG_B"
    context_args=(--kubeconfig "$KUBECONFIG_B")
  fi
  oc "${context_args[@]}" set resources deployment/microservice-b -n "$EDGE_NAMESPACE" \
    --containers=microservice-b --requests="cpu=$FAULT_CPU_REQUEST" --limits="cpu=$FAULT_CPU_LIMIT"
  oc "${context_args[@]}" rollout status deployment/microservice-b -n "$EDGE_NAMESPACE" --timeout=5m
}

if ! [[ "$CLUSTER_COUNT" =~ ^[0-9]+$ ]]; then
  echo "CLUSTER_COUNT must be an integer >= 1" >&2
  exit 2
fi
CLUSTER_COUNT=$((10#$CLUSTER_COUNT))
if (( CLUSTER_COUNT < 1 )); then
  echo "CLUSTER_COUNT must be an integer >= 1" >&2
  exit 2
fi
if [[ "$ACTION" != deploy && "$ACTION" != undeploy && "$ACTION" != reset ]]; then
  echo "Usage: $0 [deploy|undeploy|reset]" >&2
  exit 2
fi
if [[ "$ACTION" == deploy && -z "$REGISTRY" ]]; then
  echo "Set REGISTRY to the registry containing the showcase images" >&2
  exit 2
fi
if (( CLUSTER_COUNT >= 2 )) && [[ "$SITE_A" == "$SITE_B" ]]; then
  echo "SITE_A and SITE_B must differ for a multi-cluster deployment" >&2
  exit 2
fi

if [[ "$ACTION" == reset ]]; then
  reset_demo
  exit 0
fi

if (( CLUSTER_COUNT == 1 )); then
  if [[ "$ACTION" == deploy ]]; then
    deploy_single_cluster
  else
    undeploy_single_cluster
  fi
  exit 0
fi

fetch_kubeconfigs
if [[ "$ACTION" == deploy ]]; then
  deploy_multi_cluster
else
  undeploy_multi_cluster
fi
