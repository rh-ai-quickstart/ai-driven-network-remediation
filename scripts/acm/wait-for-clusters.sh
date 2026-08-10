#!/usr/bin/env bash
# Wait until each rendered ManagedCluster is Available.
#
# CLUSTER_COUNT=1  → skip, exit 0
# CLUSTER_COUNT>=2 → poll ManagedCluster condition Available=True
#
# Env:
#   SPOKES_GENERATED                 default hub/helm/spokes.generated.yaml
#   ACM_WAIT_TIMEOUT_SECONDS         default 1800 (Hive can take a while)
#   ACM_WAIT_INTERVAL_SECONDS        default 15
#   SKIP_OC_CHECK=1                  skip live wait (offline / CI)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib-spokes.sh"

CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
SPOKES_GENERATED="${SPOKES_GENERATED:-hub/helm/spokes.generated.yaml}"
TIMEOUT_SECONDS="${ACM_WAIT_TIMEOUT_SECONDS:-1800}"
INTERVAL_SECONDS="${ACM_WAIT_INTERVAL_SECONDS:-15}"
SKIP_OC_CHECK="${SKIP_OC_CHECK:-}"

log() { adnr_log "$@"; }
fail() { adnr_fail "$@"; }

managedcluster_available() {
  local oc_bin="$1" name="$2" avail
  avail="$("${oc_bin}" get managedcluster "${name}" \
    -o jsonpath='{range .status.conditions[?(@.type=="ManagedClusterConditionAvailable")]}{.status}{end}' \
    2>/dev/null || true)"
  if [[ -z "${avail}" ]]; then
    avail="$("${oc_bin}" get managedcluster "${name}" \
      -o jsonpath='{range .status.conditions[?(@.type=="Available")]}{.status}{end}' \
      2>/dev/null || true)"
  fi
  [[ "${avail}" == "True" ]]
}

if ! [[ "${CLUSTER_COUNT}" =~ ^[0-9]+$ ]] || [[ "${CLUSTER_COUNT}" -lt 1 ]]; then
  fail "CLUSTER_COUNT must be an integer >= 1 (got: ${CLUSTER_COUNT})"
fi

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  log "SKIP: wait-for-clusters (single-cluster mode, CLUSTER_COUNT=1)"
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
    log "WARN: SKIP_OC_CHECK set; would wait for: ${ADNR_SPOKE_NAMES[*]}"
    log "OK: wait-for-clusters skipped live oc (CLUSTER_COUNT=${CLUSTER_COUNT})"
    exit 0
    ;;
esac

oc_bin="$(adnr_resolve_oc)"
adnr_require_hub_login "${oc_bin}"

log "Waiting for ${#ADNR_SPOKE_NAMES[@]} ManagedCluster(s) Available (timeout=${TIMEOUT_SECONDS}s)..."
deadline=$((SECONDS + TIMEOUT_SECONDS))
pending=("${ADNR_SPOKE_NAMES[@]}")

while [[ "${#pending[@]}" -gt 0 ]]; do
  if [[ "${SECONDS}" -ge "${deadline}" ]]; then
    fail "timeout waiting for ManagedClusters Available: ${pending[*]}"
  fi

  still_pending=()
  for name in "${pending[@]}"; do
    if ! "${oc_bin}" get managedcluster "${name}" >/dev/null 2>&1; then
      log "  ${name}: ManagedCluster not found yet"
      still_pending+=("${name}")
      continue
    fi
    if managedcluster_available "${oc_bin}" "${name}"; then
      log "  ${name}: Available=True"
    else
      log "  ${name}: not Available yet"
      still_pending+=("${name}")
    fi
  done
  # Empty still_pending is a successful drain; avoid unbound array under set -u.
  if [[ "${#still_pending[@]}" -eq 0 ]]; then
    pending=()
  else
    pending=("${still_pending[@]}")
  fi
  if [[ "${#pending[@]}" -gt 0 ]]; then
    sleep "${INTERVAL_SECONDS}"
  fi
done

log "OK: wait-for-clusters — ${#ADNR_SPOKE_NAMES[@]} ManagedCluster(s) Available"
exit 0
