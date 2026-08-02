#!/usr/bin/env bash
# Label each rendered ManagedCluster for ACM Placement:
#   adnr.io/role=edge
#   cluster.open-cluster-management.io/clusterset=adnr-edge (when the set exists)
#
# CLUSTER_COUNT=1  → skip (single-cluster mode), exit 0
# CLUSTER_COUNT>=2 → label every spoke from spokes.generated.yaml
#
# The exclusive ManagedClusterSet is created by make acm-apply-placement. Role
# labels can be applied earlier; clusterset membership is applied when
# ManagedClusterSet/adnr-edge is present (acm-deploy calls this target again
# after placement).
#
# Env:
#   SPOKES_GENERATED  default hub/helm/spokes.generated.yaml
#   SKIP_OC_CHECK=1   skip live label (offline / CI)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib-spokes.sh"

CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
SPOKES_GENERATED="${SPOKES_GENERATED:-hub/helm/spokes.generated.yaml}"
SKIP_OC_CHECK="${SKIP_OC_CHECK:-}"
LABEL_KEY="adnr.io/role"
LABEL_VALUE="edge"
CLUSTERSET_NAME="adnr-edge"
CLUSTERSET_LABEL="cluster.open-cluster-management.io/clusterset"

log() { adnr_log "$@"; }
fail() { adnr_fail "$@"; }

if ! [[ "${CLUSTER_COUNT}" =~ ^[0-9]+$ ]] || [[ "${CLUSTER_COUNT}" -lt 1 ]]; then
  fail "CLUSTER_COUNT must be an integer >= 1 (got: ${CLUSTER_COUNT})"
fi

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  log "SKIP: acm-label-spokes (single-cluster mode, CLUSTER_COUNT=1)"
  exit 0
fi

adnr_require_spokes_file
adnr_load_spoke_names

if [[ "${#ADNR_SPOKE_NAMES[@]}" -eq 0 ]]; then
  fail "no spokes listed in ${SPOKES_GENERATED}"
fi

if [[ "${#ADNR_SPOKE_NAMES[@]}" -ne "${CLUSTER_COUNT}" ]]; then
  fail "spoke count mismatch: file has ${#ADNR_SPOKE_NAMES[@]}, CLUSTER_COUNT=${CLUSTER_COUNT}"
fi

skip_raw="$(printf '%s' "${SKIP_OC_CHECK}" | tr '[:upper:]' '[:lower:]')"
case "${skip_raw}" in
  1|true|yes)
    log "WARN: SKIP_OC_CHECK set; would label: ${ADNR_SPOKE_NAMES[*]} (${LABEL_KEY}=${LABEL_VALUE}, ${CLUSTERSET_LABEL}=${CLUSTERSET_NAME} when set exists)"
    log "OK: acm-label-spokes skipped live oc (CLUSTER_COUNT=${CLUSTER_COUNT})"
    exit 0
    ;;
esac

oc_bin="$(adnr_resolve_oc)"
adnr_require_hub_login "${oc_bin}"

label_clusterset=0
if "${oc_bin}" get managedclusterset "${CLUSTERSET_NAME}" >/dev/null 2>&1; then
  label_clusterset=1
else
  log "ManagedClusterSet/${CLUSTERSET_NAME} not found yet; applying ${LABEL_KEY} only (re-run after acm-apply-placement for clusterset membership)"
fi

log "Labeling ${#ADNR_SPOKE_NAMES[@]} ManagedCluster(s) ${LABEL_KEY}=${LABEL_VALUE}..."
for name in "${ADNR_SPOKE_NAMES[@]}"; do
  if ! "${oc_bin}" get managedcluster "${name}" >/dev/null 2>&1; then
    fail "ManagedCluster not found: ${name}"
  fi
  "${oc_bin}" label managedcluster "${name}" "${LABEL_KEY}=${LABEL_VALUE}" --overwrite
  if [[ "${label_clusterset}" -eq 1 ]]; then
    "${oc_bin}" label managedcluster "${name}" "${CLUSTERSET_LABEL}=${CLUSTERSET_NAME}" --overwrite
    log "  ${name}: labeled (${LABEL_KEY}=${LABEL_VALUE}, ${CLUSTERSET_LABEL}=${CLUSTERSET_NAME})"
  else
    log "  ${name}: labeled (${LABEL_KEY}=${LABEL_VALUE})"
  fi
done

log "OK: acm-label-spokes labeled ${#ADNR_SPOKE_NAMES[@]} ManagedCluster(s)"
exit 0
