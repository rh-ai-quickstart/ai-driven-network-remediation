#!/usr/bin/env bash
# Deploy the shared edge/helm chart for single-cluster (CLUSTER_COUNT=1).
#
# EDGE_GITOPS=auto   → Argo CD ApplicationSet when CRD present; else Helm (loud log)
# EDGE_GITOPS=argocd → require ApplicationSet CRD; call argocd-apply.sh
# EDGE_GITOPS=helm   → helm upgrade --install (Kind / no GitOps)
#
# Prints EDGE_DELIVERY=argocd|helm on success (last meaningful status line).
# --dry-run → print plan only; still prints EDGE_DELIVERY=
#
# Env (also forwarded to argocd-apply / helm):
#   EDGE_NAMESPACE, REGISTRY, VERSION, CLUSTER_LOG_FORWARDER_ENABLED,
#   KAFKA_EXTERNAL_HOST, EDGE_SELF_HEAL, GITOPS_*, ARGOCD_*, EDGE_HELM_RELEASE,
#   EDGE_SITE_ID (default edge-site-01), SKIP_OC_CHECK
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib-spokes.sh"

CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
EDGE_GITOPS="${EDGE_GITOPS:-auto}"
EDGE_NAMESPACE="${EDGE_NAMESPACE:-dark-noc-edge}"
EDGE_HELM_RELEASE="${EDGE_HELM_RELEASE:-adnr-edge}"
EDGE_SITE_ID="${EDGE_SITE_ID:-edge-site-01}"
REGISTRY="${REGISTRY:-quay.io/rh-ai-quickstart}"
VERSION="${VERSION:-0.1.5}"
CLUSTER_LOG_FORWARDER_ENABLED="${CLUSTER_LOG_FORWARDER_ENABLED:-false}"
EDGE_HEALER_ENABLED="${EDGE_HEALER_ENABLED:-true}"
KAFKA_EXTERNAL_HOST="${KAFKA_EXTERNAL_HOST:-}"
SKIP_OC_CHECK="${SKIP_OC_CHECK:-}"

DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      printf 'ERROR: unknown argument: %s\n' "${arg}" >&2
      exit 1
      ;;
  esac
done

log() { adnr_log "$@"; }
fail() { adnr_fail "$@"; }

if [[ "${CLUSTER_COUNT}" -ne 1 ]]; then
  fail "edge-deploy.sh is for CLUSTER_COUNT=1 only (got ${CLUSTER_COUNT}); use argocd-apply for hub-spoke"
fi

edge_gitops_raw="$(printf '%s' "${EDGE_GITOPS}" | tr '[:upper:]' '[:lower:]')"
case "${edge_gitops_raw}" in
  auto|argocd|helm) ;;
  *) fail "EDGE_GITOPS must be auto|argocd|helm (got: ${EDGE_GITOPS})" ;;
esac

skip_raw="$(printf '%s' "${SKIP_OC_CHECK}" | tr '[:upper:]' '[:lower:]')"
oc_bin=""
if command -v oc >/dev/null 2>&1; then
  oc_bin=oc
elif command -v kubectl >/dev/null 2>&1; then
  oc_bin=kubectl
fi

has_applicationset_crd() {
  [[ -n "${oc_bin}" ]] || return 1
  "${oc_bin}" get crd applicationsets.argoproj.io >/dev/null 2>&1
}

choose_delivery() {
  case "${edge_gitops_raw}" in
    helm) printf '%s' "helm" ;;
    argocd) printf '%s' "argocd" ;;
    auto)
      if [[ "${skip_raw}" == "1" || "${skip_raw}" == "true" || "${skip_raw}" == "yes" ]]; then
        # Offline / CI without a live API: prefer helm (Kind parity).
        printf '%s' "helm"
        return 0
      fi
      if has_applicationset_crd; then
        printf '%s' "argocd"
      else
        printf '%s' "helm"
      fi
      ;;
  esac
}

delivery="$(choose_delivery)"

if [[ "${edge_gitops_raw}" == "argocd" && "${delivery}" == "argocd" ]]; then
  if [[ "${skip_raw}" != "1" && "${skip_raw}" != "true" && "${skip_raw}" != "yes" ]]; then
    if ! has_applicationset_crd; then
      fail "EDGE_GITOPS=argocd but ApplicationSet CRD missing (install OpenShift GitOps)"
    fi
  fi
fi

if [[ "${edge_gitops_raw}" == "auto" && "${delivery}" == "helm" ]]; then
  log "WARN: EDGE_GITOPS=auto falling back to Helm edge install (ApplicationSet CRD missing or SKIP_OC_CHECK)"
fi

HEALER_REPO="${REGISTRY}/noc-edge-fast-path-healer"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "dry-run: EDGE_GITOPS=${EDGE_GITOPS} → delivery=${delivery}"
  if [[ "${delivery}" == "argocd" ]]; then
    log "dry-run: would run argocd-apply.sh (destination in-cluster)"
  else
    log "dry-run: would helm upgrade --install ${EDGE_HELM_RELEASE} edge/helm -n ${EDGE_NAMESPACE}"
    log "dry-run:   siteId=${EDGE_SITE_ID} clf=${CLUSTER_LOG_FORWARDER_ENABLED} healer=${EDGE_HEALER_ENABLED} image=${HEALER_REPO}:${VERSION}"
  fi
  log "EDGE_DELIVERY=${delivery}"
  log "OK: edge-deploy dry-run"
  exit 0
fi

if [[ "${delivery}" == "argocd" ]]; then
  if [[ "${skip_raw}" == "1" || "${skip_raw}" == "true" || "${skip_raw}" == "yes" ]]; then
    log "SKIP: live argocd-apply (SKIP_OC_CHECK set); EDGE_DELIVERY=argocd"
    log "EDGE_DELIVERY=argocd"
    exit 0
  fi
  log "Deploying edge via Argo CD ApplicationSet (in-cluster)..."
  # shellcheck disable=SC2086
  bash "${SCRIPT_DIR}/argocd-apply.sh" ${ARGOCD_APPLY_ARGS:-}
  log "EDGE_DELIVERY=argocd"
  log "OK: edge-deploy via Argo CD"
  exit 0
fi

if [[ "${skip_raw}" == "1" || "${skip_raw}" == "true" || "${skip_raw}" == "yes" ]]; then
  log "SKIP: live helm edge install (SKIP_OC_CHECK set); EDGE_DELIVERY=helm"
  log "EDGE_DELIVERY=helm"
  exit 0
fi

if ! command -v helm >/dev/null 2>&1; then
  fail "helm not found on PATH (required for EDGE_GITOPS=helm / auto fallback)"
fi

if [[ -z "${oc_bin}" ]]; then
  fail "oc or kubectl not found on PATH"
fi

# Helm --create-namespace owns the release namespace. Disable the chart's Namespace
# template so we do not double-create (Helm fails with "already exists").
log "Deploying edge via Helm (${EDGE_HELM_RELEASE} in ${EDGE_NAMESPACE})..."
helm_args=(
  upgrade --install "${EDGE_HELM_RELEASE}" edge/helm
  --namespace "${EDGE_NAMESPACE}"
  --create-namespace
  --set "createNamespace=false"
  --set "siteId=${EDGE_SITE_ID}"
  --set "namespace=${EDGE_NAMESPACE}"
  --set "fastPathHealer.image.repository=${HEALER_REPO}"
  --set "fastPathHealer.image.tag=${VERSION}"
  --set "fastPathHealer.enabled=${EDGE_HEALER_ENABLED}"
  --set "clusterLogForwarder.enabled=${CLUSTER_LOG_FORWARDER_ENABLED}"
  --wait --timeout 5m
)
if [[ -n "${KAFKA_EXTERNAL_HOST}" ]]; then
  helm_args+=(--set "kafka.externalHost=${KAFKA_EXTERNAL_HOST}")
elif [[ "${CLUSTER_LOG_FORWARDER_ENABLED}" == "true" || "${CLUSTER_LOG_FORWARDER_ENABLED}" == "TRUE" || "${CLUSTER_LOG_FORWARDER_ENABLED}" == "1" ]]; then
  fail "KAFKA_EXTERNAL_HOST required when clusterLogForwarder.enabled=true for Helm edge install"
fi

helm "${helm_args[@]}"
log "EDGE_DELIVERY=helm"
log "OK: edge-deploy via Helm"
exit 0
