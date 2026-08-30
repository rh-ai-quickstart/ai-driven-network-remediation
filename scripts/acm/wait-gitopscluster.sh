#!/usr/bin/env bash
# Wait until GitOpsCluster/adnr-edge reports ArgoServerVerified and Ready.
#
# CLUSTER_COUNT=1  → skip, exit 0
# CLUSTER_COUNT>=2 → poll GitOpsCluster conditions before argocd-apply
#
# Env:
#   NAMESPACE                        hub install namespace (default hub)
#   GITOPSCLUSTER_WAIT_TIMEOUT_SECONDS   default 300
#   GITOPSCLUSTER_WAIT_INTERVAL_SECONDS  default 10
#   SKIP_OC_CHECK=1                  skip live wait (offline / CI)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib-spokes.sh"

CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
NAMESPACE="${NAMESPACE:-hub}"
GITOPSCLUSTER_NAME="adnr-edge"
TIMEOUT_SECONDS="${GITOPSCLUSTER_WAIT_TIMEOUT_SECONDS:-300}"
INTERVAL_SECONDS="${GITOPSCLUSTER_WAIT_INTERVAL_SECONDS:-10}"
SKIP_OC_CHECK="${SKIP_OC_CHECK:-}"

log() { adnr_log "$@"; }
fail() { adnr_fail "$@"; }

gitopscluster_condition_status() {
  local oc_bin="$1" ns="$2" name="$3" cond_type="$4"
  "${oc_bin}" get gitopscluster "${name}" -n "${ns}" \
    -o "jsonpath={range .status.conditions[?(@.type==\"${cond_type}\")]}{.status}{end}" \
    2>/dev/null || true
}

gitopscluster_condition_message() {
  local oc_bin="$1" ns="$2" name="$3" cond_type="$4"
  "${oc_bin}" get gitopscluster "${name}" -n "${ns}" \
    -o "jsonpath={range .status.conditions[?(@.type==\"${cond_type}\")]}{.message}{end}" \
    2>/dev/null || true
}

gitopscluster_ready() {
  local oc_bin="$1" ns="$2" name="$3" argo ready
  argo="$(gitopscluster_condition_status "${oc_bin}" "${ns}" "${name}" "ArgoServerVerified")"
  ready="$(gitopscluster_condition_status "${oc_bin}" "${ns}" "${name}" "Ready")"
  [[ "${argo}" == "True" && "${ready}" == "True" ]]
}

dump_gitopscluster_diagnostics() {
  local oc_bin="$1" ns="$2" name="$3" cond argo_msg ready_msg
  if ! "${oc_bin}" get gitopscluster "${name}" -n "${ns}" >/dev/null 2>&1; then
    log "  gitopscluster/${name}: not found in ${ns}"
    return 0
  fi
  log "  gitopscluster/${name} conditions:"
  while IFS= read -r cond; do
    [[ -n "${cond}" ]] || continue
    log "    ${cond}"
  done < <("${oc_bin}" get gitopscluster "${name}" -n "${ns}" \
    -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.message}){"\n"}{end}' \
    2>/dev/null || true)
  argo_msg="$(gitopscluster_condition_message "${oc_bin}" "${ns}" "${name}" "ArgoServerVerified")"
  ready_msg="$(gitopscluster_condition_message "${oc_bin}" "${ns}" "${name}" "Ready")"
  if [[ -n "${argo_msg}" ]]; then
    log "    ArgoServerVerified: ${argo_msg}"
  fi
  if [[ -n "${ready_msg}" ]]; then
    log "    Ready: ${ready_msg}"
  fi
}

if ! [[ "${CLUSTER_COUNT}" =~ ^[0-9]+$ ]] || [[ "${CLUSTER_COUNT}" -lt 1 ]]; then
  fail "CLUSTER_COUNT must be an integer >= 1 (got: ${CLUSTER_COUNT})"
fi

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  log "SKIP: wait-gitopscluster (single-cluster mode, CLUSTER_COUNT=1)"
  exit 0
fi

skip_raw="$(printf '%s' "${SKIP_OC_CHECK}" | tr '[:upper:]' '[:lower:]')"
case "${skip_raw}" in
  1|true|yes)
    log "WARN: SKIP_OC_CHECK set; would wait for GitOpsCluster/${GITOPSCLUSTER_NAME} Ready in ${NAMESPACE}"
    log "OK: wait-gitopscluster skipped live oc (CLUSTER_COUNT=${CLUSTER_COUNT})"
    exit 0
    ;;
esac

oc_bin="$(adnr_resolve_oc)"
adnr_require_hub_login "${oc_bin}"

log "Waiting for GitOpsCluster/${GITOPSCLUSTER_NAME} in ${NAMESPACE} (ArgoServerVerified=True, Ready=True, timeout=${TIMEOUT_SECONDS}s)..."
deadline=$((SECONDS + TIMEOUT_SECONDS))

while true; do
  if ! "${oc_bin}" get gitopscluster "${GITOPSCLUSTER_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    log "  gitopscluster/${GITOPSCLUSTER_NAME}: not found yet"
  elif gitopscluster_ready "${oc_bin}" "${NAMESPACE}" "${GITOPSCLUSTER_NAME}"; then
    log "  gitopscluster/${GITOPSCLUSTER_NAME}: ArgoServerVerified=True Ready=True"
    log "OK: wait-gitopscluster — GitOpsCluster/${GITOPSCLUSTER_NAME} ready"
    exit 0
  else
    argo="$(gitopscluster_condition_status "${oc_bin}" "${NAMESPACE}" "${GITOPSCLUSTER_NAME}" "ArgoServerVerified")"
    ready="$(gitopscluster_condition_status "${oc_bin}" "${NAMESPACE}" "${GITOPSCLUSTER_NAME}" "Ready")"
    log "  gitopscluster/${GITOPSCLUSTER_NAME}: ArgoServerVerified=${argo:-unknown} Ready=${ready:-unknown}"
  fi

  if [[ "${SECONDS}" -ge "${deadline}" ]]; then
    log "ERROR: timeout waiting for GitOpsCluster/${GITOPSCLUSTER_NAME} Ready"
    dump_gitopscluster_diagnostics "${oc_bin}" "${NAMESPACE}" "${GITOPSCLUSTER_NAME}"
    log "HINT: missing openshift-gitops label apps.open-cluster-management.io/gitops-argo-namespace=true is a common cause."
    exit 1
  fi
  sleep "${INTERVAL_SECONDS}"
done
