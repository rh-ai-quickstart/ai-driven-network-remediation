#!/usr/bin/env bash
# Tear down ACM hub-spoke ADNR resources (reverse of acm-deploy hub-spoke path).
#
# Order: ArgoCD apps → ACM placement/policy → optional ManifestWorks →
#        hub helm-uninstall → optional Hive ClusterDeployments when CLUSTER_CREATE=true
#
# CLUSTER_COUNT=1  → skip ACM pieces (caller should still run helm-uninstall)
# --dry-run        → print actions only
#
# Env:
#   SPOKES_GENERATED, NAMESPACE, EDGE_NAMESPACE, CLUSTER_CREATE, ARGOCD_NAMESPACE
#   SKIP_OC_CHECK=1  offline skip
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib-spokes.sh"

CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
SPOKES_GENERATED="${SPOKES_GENERATED:-hub/helm/spokes.generated.yaml}"
NAMESPACE="${NAMESPACE:-hub}"
EDGE_NAMESPACE="${EDGE_NAMESPACE:-dark-noc-edge}"
CLUSTER_CREATE="${CLUSTER_CREATE:-false}"
ARGOCD_NAMESPACE="${ARGOCD_NAMESPACE:-}"
ACM_DIR="${ACM_DIR:-cross-cluster/acm}"
SKIP_OC_CHECK="${SKIP_OC_CHECK:-}"
MANIFESTWORK_NAME="adnr-kafka-client-certs"

DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
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

run() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "dry-run: $*"
    return 0
  fi
  "$@"
}

if ! [[ "${CLUSTER_COUNT}" =~ ^[0-9]+$ ]] || [[ "${CLUSTER_COUNT}" -lt 1 ]]; then
  fail "CLUSTER_COUNT must be an integer >= 1 (got: ${CLUSTER_COUNT})"
fi

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  log "SKIP: acm-teardown ACM/ArgoCD steps (single-cluster mode); use make helm-uninstall"
  exit 0
fi

skip_raw="$(printf '%s' "${SKIP_OC_CHECK}" | tr '[:upper:]' '[:lower:]')"
case "${skip_raw}" in
  1|true|yes)
    log "WARN: SKIP_OC_CHECK set; acm-teardown live deletes skipped"
    log "OK: acm-teardown skipped live oc (CLUSTER_COUNT=${CLUSTER_COUNT})"
    exit 0
    ;;
esac

oc_bin="$(adnr_resolve_oc)"
adnr_require_hub_login "${oc_bin}"

ADNR_SPOKE_NAMES=()
if [[ -f "${SPOKES_GENERATED}" ]]; then
  adnr_load_spoke_names
else
  log "WARN: ${SPOKES_GENERATED} missing; Hive/ManifestWork cleanup may be incomplete"
fi

detect_argocd_namespace() {
  if [[ -n "${ARGOCD_NAMESPACE}" ]]; then
    printf '%s' "${ARGOCD_NAMESPACE}"
    return 0
  fi
  if "${oc_bin}" get namespace openshift-gitops >/dev/null 2>&1; then
    printf '%s' "openshift-gitops"
    return 0
  fi
  if "${oc_bin}" get namespace argocd >/dev/null 2>&1; then
    printf '%s' "argocd"
    return 0
  fi
  printf '%s' ""
}

argocd_ns="$(detect_argocd_namespace)"
if [[ -n "${argocd_ns}" ]]; then
  log "Deleting ArgoCD ApplicationSet + AppProject in ${argocd_ns}..."
  run "${oc_bin}" delete applicationset adnr-edge -n "${argocd_ns}" --ignore-not-found
  # Applications created by the set (best-effort)
  if [[ "${#ADNR_SPOKE_NAMES[@]}" -gt 0 ]]; then
    for name in "${ADNR_SPOKE_NAMES[@]}"; do
      run "${oc_bin}" delete application "adnr-edge-${name}" -n "${argocd_ns}" --ignore-not-found
    done
  fi
  run "${oc_bin}" delete appproject adnr-edge -n "${argocd_ns}" --ignore-not-found
else
  log "WARN: ArgoCD namespace not found; skipping ApplicationSet/AppProject delete"
fi

log "Deleting ACM Placement / ManagedClusterSet / Policy..."
# Do not apply/delete clusterdeployment.yaml (Hive template with placeholders).
for f in placement.yaml namespace-policy.yaml; do
  if [[ -f "${ACM_DIR}/${f}" ]]; then
    run "${oc_bin}" delete -f "${ACM_DIR}/${f}" --ignore-not-found
  fi
done

if [[ "${#ADNR_SPOKE_NAMES[@]}" -gt 0 ]]; then
  for name in "${ADNR_SPOKE_NAMES[@]}"; do
    run "${oc_bin}" delete manifestwork "${MANIFESTWORK_NAME}" -n "${name}" --ignore-not-found
  done
fi

create_raw="$(printf '%s' "${CLUSTER_CREATE}" | tr '[:upper:]' '[:lower:]')"
case "${create_raw}" in
  1|true|yes)
    log "CLUSTER_CREATE=true: deleting Hive ClusterDeployments for spokes..."
    if [[ "${#ADNR_SPOKE_NAMES[@]}" -gt 0 ]]; then
      for name in "${ADNR_SPOKE_NAMES[@]}"; do
        run "${oc_bin}" delete clusterdeployment "${name}" -n "${name}" --ignore-not-found
      done
    fi
    ;;
  *)
    log "CLUSTER_CREATE=${CLUSTER_CREATE}: leaving ManagedClusters / Hive resources in place"
    ;;
esac

log "OK: acm-teardown ACM/ArgoCD cleanup done (run make helm-uninstall for hub chart)"
log "NOTE: spoke namespaces (${EDGE_NAMESPACE}) and ManagedCluster labels are not removed"
exit 0
