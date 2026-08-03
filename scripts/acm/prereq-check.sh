#!/usr/bin/env bash
# Verify ACM / ArgoCD / ManagedCluster readiness for hub-spoke deploy.
#
# CLUSTER_COUNT=1  → skip (single-cluster mode), exit 0
# CLUSTER_COUNT>=2 → require ACM operator, ArgoCD, and N Available ManagedClusters
#                    matching names from hub/helm/spokes.generated.yaml
# CLUSTER_CREATE=true → still require ACM + ArgoCD; skip ManagedCluster Available
#                       checks (Hive will create them; wait-for-clusters follows)
# SKIP_OC_CHECK=1  → still validates spoke list; skips live cluster probes
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib-spokes.sh"

CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
CLUSTER_CREATE="${CLUSTER_CREATE:-false}"
SPOKES_GENERATED="${SPOKES_GENERATED:-hub/helm/spokes.generated.yaml}"
SKIP_OC_CHECK="${SKIP_OC_CHECK:-}"

log() { adnr_log "$@"; }
fail() { adnr_fail "$@"; }

if ! [[ "${CLUSTER_COUNT}" =~ ^[0-9]+$ ]] || [[ "${CLUSTER_COUNT}" -lt 1 ]]; then
  fail "CLUSTER_COUNT must be an integer >= 1 (got: ${CLUSTER_COUNT})"
fi

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  log "SKIP: acm-prereq-check (single-cluster mode, CLUSTER_COUNT=1)"
  exit 0
fi

adnr_require_spokes_file
adnr_load_spoke_names
expected_spokes=("${ADNR_SPOKE_NAMES[@]}")

if [[ "${#expected_spokes[@]}" -eq 0 ]]; then
  fail "no spokes listed in ${SPOKES_GENERATED} (expected CLUSTER_COUNT=${CLUSTER_COUNT})"
fi

if [[ "${#expected_spokes[@]}" -ne "${CLUSTER_COUNT}" ]]; then
  fail "spoke count mismatch: file has ${#expected_spokes[@]}, CLUSTER_COUNT=${CLUSTER_COUNT}"
fi

log "expected spokes: ${expected_spokes[*]}"

skip_raw="$(printf '%s' "${SKIP_OC_CHECK}" | tr '[:upper:]' '[:lower:]')"
skip_oc=0
case "${skip_raw}" in
  1|true|yes) skip_oc=1 ;;
esac

if [[ "${skip_oc}" -eq 1 ]]; then
  log "WARN: SKIP_OC_CHECK set; skipping live ACM/ArgoCD/ManagedCluster checks"
  log "OK: acm-prereq-check spoke list OK, live checks skipped (CLUSTER_COUNT=${CLUSTER_COUNT})"
  exit 0
fi

oc_bin="$(adnr_resolve_oc)"

if ! "${oc_bin}" whoami >/dev/null 2>&1; then
  fail "not logged into hub cluster (${oc_bin} whoami failed)"
fi

log "Checking ACM operator CRDs..."
for crd in managedclusters.cluster.open-cluster-management.io \
  placements.cluster.open-cluster-management.io \
  managedclustersets.cluster.open-cluster-management.io; do
  if ! "${oc_bin}" get crd "${crd}" >/dev/null 2>&1; then
    fail "ACM CRD missing: ${crd}"
  fi
done
log "ACM operator: OK (required CRDs present)"

log "Checking ArgoCD / OpenShift GitOps..."
argocd_ok=0
if "${oc_bin}" get crd applications.argoproj.io >/dev/null 2>&1; then
  argocd_ok=1
elif "${oc_bin}" get namespace openshift-gitops >/dev/null 2>&1; then
  argocd_ok=1
elif "${oc_bin}" get namespace argocd >/dev/null 2>&1; then
  argocd_ok=1
fi
if [[ "${argocd_ok}" -ne 1 ]]; then
  fail "ArgoCD/GitOps not found (no applications.argoproj.io CRD and no openshift-gitops/argocd namespace)"
fi
# ApplicationSet is required for edge fan-out (make argocd-apply).
if ! "${oc_bin}" get crd applicationsets.argoproj.io >/dev/null 2>&1; then
  fail "ArgoCD ApplicationSet CRD missing: applicationsets.argoproj.io (enable ApplicationSet on OpenShift GitOps)"
fi
log "ArgoCD/GitOps: OK (Application + ApplicationSet CRDs)"

create_raw="$(printf '%s' "${CLUSTER_CREATE}" | tr '[:upper:]' '[:lower:]')"
create_enabled=0
case "${create_raw}" in
  1|true|yes) create_enabled=1 ;;
esac

if [[ "${create_enabled}" -eq 1 ]]; then
  log "CLUSTER_CREATE=true: skipping ManagedCluster Available checks (Hive will provision)"
  log "OK: acm-prereq-check passed (ACM + ArgoCD ready; ${#expected_spokes[@]} spokes expected after create)"
  exit 0
fi

log "Checking ManagedClusters Available for ${#expected_spokes[@]} spoke(s)..."
missing=()
not_available=()
for name in "${expected_spokes[@]}"; do
  if ! "${oc_bin}" get managedcluster "${name}" >/dev/null 2>&1; then
    missing+=("${name}")
    continue
  fi
  avail="$("${oc_bin}" get managedcluster "${name}" \
    -o jsonpath='{range .status.conditions[?(@.type=="ManagedClusterConditionAvailable")]}{.status}{end}' \
    2>/dev/null || true)"
  if [[ -z "${avail}" ]]; then
    avail="$("${oc_bin}" get managedcluster "${name}" \
      -o jsonpath='{range .status.conditions[?(@.type=="Available")]}{.status}{end}' \
      2>/dev/null || true)"
  fi
  if [[ "${avail}" != "True" ]]; then
    not_available+=("${name}(Available=${avail:-unknown})")
  else
    log "  ${name}: Available=True"
  fi
done

if [[ "${#missing[@]}" -gt 0 ]]; then
  fail "ManagedCluster(s) not found: ${missing[*]} (import spokes or set CLUSTER_CREATE=true)"
fi
if [[ "${#not_available[@]}" -gt 0 ]]; then
  fail "ManagedCluster(s) not Available: ${not_available[*]}"
fi

log "OK: acm-prereq-check passed (${#expected_spokes[@]} ManagedClusters Available)"
exit 0
