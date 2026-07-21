#!/usr/bin/env bash
# Wait until ADNR edge Applications are Synced and Healthy for each spoke.
#
# CLUSTER_COUNT=1  → skip, exit 0
# CLUSTER_COUNT>=2 → poll Applications adnr-edge-<spoke-name> in ArgoCD namespace
#
# Env:
#   SPOKES_GENERATED   default hub/helm/spokes.generated.yaml
#   ARGOCD_NAMESPACE   optional (else detect openshift-gitops|argocd)
#   ARGOCD_WAIT_TIMEOUT_SECONDS  default 600
#   ARGOCD_WAIT_INTERVAL_SECONDS default 10
set -euo pipefail

CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
SPOKES_GENERATED="${SPOKES_GENERATED:-hub/helm/spokes.generated.yaml}"
ARGOCD_NAMESPACE="${ARGOCD_NAMESPACE:-}"
TIMEOUT_SECONDS="${ARGOCD_WAIT_TIMEOUT_SECONDS:-600}"
INTERVAL_SECONDS="${ARGOCD_WAIT_INTERVAL_SECONDS:-10}"

log() { printf '%s\n' "$*"; }
fail() { log "ERROR: $*"; exit 1; }

if ! [[ "${CLUSTER_COUNT}" =~ ^[0-9]+$ ]] || [[ "${CLUSTER_COUNT}" -lt 1 ]]; then
  fail "CLUSTER_COUNT must be an integer >= 1 (got: ${CLUSTER_COUNT})"
fi

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  log "SKIP: argocd-wait-spokes (single-cluster mode, CLUSTER_COUNT=1)"
  exit 0
fi

if [[ ! -f "${SPOKES_GENERATED}" ]]; then
  fail "missing ${SPOKES_GENERATED}; run 'make validate-topology CLUSTER_COUNT=${CLUSTER_COUNT}' first"
fi

spokes=()
while IFS= read -r _spoke; do
  [[ -n "${_spoke}" ]] && spokes+=("${_spoke}")
done < <(
  awk '
    /^[[:space:]]*spokes:[[:space:]]*\[\][[:space:]]*$/ { exit }
    /^[[:space:]]*- name:[[:space:]]+/ {
      name = $0
      sub(/^[[:space:]]*- name:[[:space:]]+/, "", name)
      gsub(/[[:space:]]+$/, "", name)
      if (name != "") print name
    }
  ' "${SPOKES_GENERATED}"
)

if [[ "${#spokes[@]}" -eq 0 ]]; then
  fail "no spokes listed in ${SPOKES_GENERATED}"
fi

if [[ "${#spokes[@]}" -ne "${CLUSTER_COUNT}" ]]; then
  fail "spoke count mismatch: file has ${#spokes[@]}, CLUSTER_COUNT=${CLUSTER_COUNT}"
fi

oc_bin=""
if command -v oc >/dev/null 2>&1; then
  oc_bin=oc
elif command -v kubectl >/dev/null 2>&1; then
  oc_bin=kubectl
else
  fail "oc or kubectl not found on PATH"
fi

if ! "${oc_bin}" whoami >/dev/null 2>&1; then
  fail "not logged into hub cluster (${oc_bin} whoami failed)"
fi

argocd_ns="${ARGOCD_NAMESPACE}"
if [[ -z "${argocd_ns}" ]]; then
  if "${oc_bin}" get namespace openshift-gitops >/dev/null 2>&1; then
    argocd_ns=openshift-gitops
  elif "${oc_bin}" get namespace argocd >/dev/null 2>&1; then
    argocd_ns=argocd
  else
    fail "ArgoCD namespace not found (openshift-gitops or argocd); set ARGOCD_NAMESPACE"
  fi
fi

log "Waiting for ${#spokes[@]} Application(s) in ${argocd_ns} (timeout=${TIMEOUT_SECONDS}s)..."

dump_app_diagnostics() {
  local name="$1"
  local app="adnr-edge-${name}"
  if ! "${oc_bin}" get application "${app}" -n "${argocd_ns}" >/dev/null 2>&1; then
    log "  ${app}: Application not found in ${argocd_ns}"
    return 0
  fi
  local sync health message
  sync="$("${oc_bin}" get application "${app}" -n "${argocd_ns}" \
    -o jsonpath='{.status.sync.status}' 2>/dev/null || true)"
  health="$("${oc_bin}" get application "${app}" -n "${argocd_ns}" \
    -o jsonpath='{.status.health.status}' 2>/dev/null || true)"
  message="$("${oc_bin}" get application "${app}" -n "${argocd_ns}" \
    -o jsonpath='{.status.conditions[?(@.type=="ComparisonError")].message}' 2>/dev/null || true)"
  if [[ -z "${message}" ]]; then
    message="$("${oc_bin}" get application "${app}" -n "${argocd_ns}" \
      -o jsonpath='{.status.operationState.message}' 2>/dev/null || true)"
  fi
  log "  ${app}: sync=${sync:-unknown} health=${health:-unknown}"
  if [[ -n "${message}" ]]; then
    log "    message: ${message}"
  fi
}

deadline=$((SECONDS + TIMEOUT_SECONDS))
pending=("${spokes[@]}")

while [[ "${#pending[@]}" -gt 0 ]]; do
  if [[ "${SECONDS}" -ge "${deadline}" ]]; then
    log "ERROR: timeout waiting for Applications Synced/Healthy: ${pending[*]}"
    log "Diagnostics:"
    for name in "${pending[@]}"; do
      dump_app_diagnostics "${name}"
    done
    log "HINT: empty kafka.externalHost, missing ArgoCD cluster secret, or edge chart render errors commonly leave apps Unhealthy."
    exit 1
  fi

  still_pending=()
  for name in "${pending[@]}"; do
    app="adnr-edge-${name}"
    if ! "${oc_bin}" get application "${app}" -n "${argocd_ns}" >/dev/null 2>&1; then
      log "  ${app}: not found yet"
      still_pending+=("${name}")
      continue
    fi
    sync="$("${oc_bin}" get application "${app}" -n "${argocd_ns}" \
      -o jsonpath='{.status.sync.status}' 2>/dev/null || true)"
    health="$("${oc_bin}" get application "${app}" -n "${argocd_ns}" \
      -o jsonpath='{.status.health.status}' 2>/dev/null || true)"
    if [[ "${sync}" == "Synced" && "${health}" == "Healthy" ]]; then
      log "  ${app}: Synced Healthy"
    else
      log "  ${app}: sync=${sync:-unknown} health=${health:-unknown}"
      still_pending+=("${name}")
    fi
  done
  pending=("${still_pending[@]}")
  if [[ "${#pending[@]}" -gt 0 ]]; then
    sleep "${INTERVAL_SECONDS}"
  fi
done

log "OK: argocd-wait-spokes — ${#spokes[@]} Application(s) Synced Healthy"
exit 0
