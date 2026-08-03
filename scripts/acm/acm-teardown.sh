#!/usr/bin/env bash
# Tear down ACM hub-spoke ADNR resources (reverse of acm-deploy hub-spoke path).
#
# Order: ACM policy (stop recreating edge ns) → GitOpsCluster → Placement /
#        ManagedClusterSet → ArgoCD apps (prune spokes) → spoke edge namespaces →
#        ManifestWorks → optional Hive ClusterDeployments.
# Caller (make acm-teardown) then runs hub helm-uninstall (skipped on --dry-run).
#
# CLUSTER_COUNT=1  → skip ACM pieces (caller should still run helm-uninstall)
# --dry-run        → print actions only; make also skips helm-uninstall
#
# Env:
#   SPOKES_GENERATED, NAMESPACE, EDGE_NAMESPACE, CLUSTER_CREATE, ARGOCD_NAMESPACE
#   SKIP_OC_CHECK=1  offline skip
#   ACM_TEARDOWN_APP_TIMEOUT_SECONDS  wait for ArgoCD app prune (default 300)
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
APP_TIMEOUT_SECONDS="${ACM_TEARDOWN_APP_TIMEOUT_SECONDS:-300}"
ARGOCD_APP_FINALIZER="resources-finalizer.argocd.argoproj.io"
# Prefer argoproj.io: bare "application" can resolve to app.k8s.io on ACM hubs.
ARGOCD_APP_RESOURCE="applications.argoproj.io"
ARGOCD_APPSET_RESOURCE="applicationsets.argoproj.io"
ARGOCD_APPPROJECT_RESOURCE="appprojects.argoproj.io"

DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
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
    if [[ "${DRY_RUN}" -eq 1 ]]; then
      log "WARN: SKIP_OC_CHECK set; continuing dry-run plan only (no oc login)"
    else
      log "WARN: SKIP_OC_CHECK set; acm-teardown live deletes skipped"
      log "OK: acm-teardown skipped live oc (CLUSTER_COUNT=${CLUSTER_COUNT})"
      exit 0
    fi
    ;;
esac

oc_bin="$(adnr_resolve_oc)"
if [[ "${DRY_RUN}" -eq 0 ]]; then
  adnr_require_hub_login "${oc_bin}"
else
  log "dry-run: skipping oc login check"
fi

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
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    # Offline dry-run: prefer the OpenShift GitOps default without calling oc.
    printf '%s' "openshift-gitops"
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

extract_spoke_kubeconfig() {
  local spoke="$1" out="$2" secret
  for secret in "${spoke}-admin-kubeconfig" "${spoke}-kubeconfig"; do
    if "${oc_bin}" get secret "${secret}" -n "${spoke}" >/dev/null 2>&1; then
      if "${oc_bin}" get secret "${secret}" -n "${spoke}" \
        -o jsonpath='{.data.kubeconfig}' 2>/dev/null | base64 -d > "${out}" \
        && [[ -s "${out}" ]]; then
        log "  ${spoke}: using Hive/import secret ${secret}"
        return 0
      fi
    fi
  done
  # Laptop-usable only when secret holds a full admin kubeconfig (not cluster-proxy).
  secret="noc-openshift-kubeconfig-${spoke}"
  if "${oc_bin}" get secret "${secret}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    if "${oc_bin}" get secret "${secret}" -n "${NAMESPACE}" \
      -o jsonpath='{.data.kubeconfig}' 2>/dev/null | base64 -d > "${out}" \
      && [[ -s "${out}" ]]; then
      log "  ${spoke}: using hub secret ${secret}"
      return 0
    fi
  fi
  return 1
}

ensure_app_prune_finalizer() {
  local app="$1" ns="$2" current
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "dry-run: would patch ${ARGOCD_APP_RESOURCE}/${app} finalizer ${ARGOCD_APP_FINALIZER}"
    return 0
  fi
  if ! "${oc_bin}" get "${ARGOCD_APP_RESOURCE}" "${app}" -n "${ns}" >/dev/null 2>&1; then
    return 0
  fi
  current="$("${oc_bin}" get "${ARGOCD_APP_RESOURCE}" "${app}" -n "${ns}" \
    -o jsonpath='{.metadata.finalizers}' 2>/dev/null || true)"
  if printf '%s' "${current}" | grep -Fq "${ARGOCD_APP_FINALIZER}"; then
    return 0
  fi
  if [[ -z "${current}" || "${current}" == "[]" ]]; then
    "${oc_bin}" patch "${ARGOCD_APP_RESOURCE}" "${app}" -n "${ns}" --type=merge \
      -p "{\"metadata\":{\"finalizers\":[\"${ARGOCD_APP_FINALIZER}\"]}}" \
      >/dev/null 2>&1 || true
  else
    "${oc_bin}" patch "${ARGOCD_APP_RESOURCE}" "${app}" -n "${ns}" --type=json \
      -p "[{\"op\":\"add\",\"path\":\"/metadata/finalizers/-\",\"value\":\"${ARGOCD_APP_FINALIZER}\"}]" \
      >/dev/null 2>&1 || true
  fi
}

wait_for_applications_gone() {
  local ns="$1"
  local deadline remaining app
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "dry-run: would wait for adnr-edge Applications to finish pruning"
    return 0
  fi
  if [[ "${#ADNR_SPOKE_NAMES[@]}" -eq 0 ]]; then
    return 0
  fi
  deadline=$((SECONDS + APP_TIMEOUT_SECONDS))
  log "Waiting for ArgoCD Applications to prune + delete (timeout=${APP_TIMEOUT_SECONDS}s)..."
  while true; do
    remaining=0
    for name in "${ADNR_SPOKE_NAMES[@]}"; do
      app="adnr-edge-${name}"
      if "${oc_bin}" get "${ARGOCD_APP_RESOURCE}" "${app}" -n "${ns}" >/dev/null 2>&1; then
        remaining=$((remaining + 1))
      fi
    done
    if [[ "${remaining}" -eq 0 ]]; then
      log "ArgoCD edge Applications gone"
      return 0
    fi
    if [[ "${SECONDS}" -ge "${deadline}" ]]; then
      log "WARN: ${remaining} ArgoCD Application(s) still present after ${APP_TIMEOUT_SECONDS}s; continuing"
      return 0
    fi
    sleep 5
  done
}

delete_spoke_edge_namespace() {
  local spoke="$1" kc="$2"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "dry-run: would delete namespace ${EDGE_NAMESPACE} on ${spoke}"
    return 0
  fi
  if KUBECONFIG="${kc}" "${oc_bin}" delete namespace "${EDGE_NAMESPACE}" --ignore-not-found --wait=false; then
    log "  ${spoke}: delete requested for namespace/${EDGE_NAMESPACE}"
  else
    log "WARN: ${spoke}: failed to delete namespace/${EDGE_NAMESPACE}"
  fi
}

# ── 1. ACM namespaced objects (reverse of acm-apply-placement) ──
# Policy first: musthave Namespace would recreate dark-noc-edge.
# GitOpsCluster before Placement (placementRef dependency).
# Do not apply/delete clusterdeployment.yaml (Hive template with placeholders).
log "Deleting ACM Policy / GitOpsCluster / Placement / ManagedClusterSet..."
for f in namespace-policy.yaml gitopscluster.yaml placement.yaml; do
  if [[ -f "${ACM_DIR}/${f}" ]]; then
    run "${oc_bin}" delete -f "${ACM_DIR}/${f}" --ignore-not-found
  fi
done

# ── 2. ArgoCD: patch prune finalizer, delete ApplicationSet + apps, wait ──
argocd_ns="$(detect_argocd_namespace)"
if [[ -n "${argocd_ns}" ]]; then
  log "Deleting ArgoCD ApplicationSet + AppProject in ${argocd_ns}..."
  if [[ "${#ADNR_SPOKE_NAMES[@]}" -gt 0 ]]; then
    for name in "${ADNR_SPOKE_NAMES[@]}"; do
      ensure_app_prune_finalizer "adnr-edge-${name}" "${argocd_ns}"
    done
  fi
  # Delete ApplicationSet first so it cannot recreate Applications.
  run "${oc_bin}" delete "${ARGOCD_APPSET_RESOURCE}" adnr-edge -n "${argocd_ns}" --ignore-not-found
  if [[ "${#ADNR_SPOKE_NAMES[@]}" -gt 0 ]]; then
    for name in "${ADNR_SPOKE_NAMES[@]}"; do
      run "${oc_bin}" delete "${ARGOCD_APP_RESOURCE}" "adnr-edge-${name}" -n "${argocd_ns}" --ignore-not-found --wait=false
    done
  fi
  wait_for_applications_gone "${argocd_ns}"
  run "${oc_bin}" delete "${ARGOCD_APPPROJECT_RESOURCE}" adnr-edge -n "${argocd_ns}" --ignore-not-found
else
  log "WARN: ArgoCD namespace not found; skipping ApplicationSet/AppProject delete"
fi

# ── 3. Explicit spoke edge namespace cleanup (covers apps without finalizer) ──
tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

if [[ "${#ADNR_SPOKE_NAMES[@]}" -gt 0 ]]; then
  log "Deleting spoke namespaces (${EDGE_NAMESPACE})..."
  for name in "${ADNR_SPOKE_NAMES[@]}"; do
    if [[ "${DRY_RUN}" -eq 1 ]]; then
      log "dry-run: would delete namespace ${EDGE_NAMESPACE} on ${name}"
      continue
    fi
    kc="${tmpdir}/kubeconfig-${name}"
    if extract_spoke_kubeconfig "${name}" "${kc}"; then
      delete_spoke_edge_namespace "${name}" "${kc}"
    else
      log "WARN: ${name}: no spoke kubeconfig; skip direct namespace delete (ArgoCD prune / ManifestWork may still clean)"
    fi
  done
fi

# ── 4. Kafka cert ManifestWorks (cascades Secret/Namespace they applied) ──
if [[ "${#ADNR_SPOKE_NAMES[@]}" -gt 0 ]]; then
  log "Deleting ManifestWork/${MANIFESTWORK_NAME} on spokes..."
  for name in "${ADNR_SPOKE_NAMES[@]}"; do
    run "${oc_bin}" delete manifestwork "${MANIFESTWORK_NAME}" -n "${name}" --ignore-not-found
  done
fi

# ── 5. Optional Hive ClusterDeployments ──
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

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "OK: acm-teardown dry-run complete (make acm-teardown also skips helm-uninstall)"
else
  log "OK: acm-teardown ACM/ArgoCD + spoke edge cleanup done (run make helm-uninstall for hub chart)"
fi
exit 0
