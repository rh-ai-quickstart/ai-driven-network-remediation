#!/usr/bin/env bash
# Render and apply ACM Placement, GitOpsCluster, and namespace Policy.
#
# CLUSTER_COUNT=1  → skip (single-cluster mode), exit 0
# CLUSTER_COUNT>=2 → substitute placeholders and apply (or --dry-run)
#
# Steps (make acm-apply-placement runs placement → label-spokes → remaining):
#   --step=placement  ManagedClusterSet + Binding + Placement
#   --step=remaining  GitOpsCluster + namespace Policy
#   --step=all        everything (default; used by dry-run / standalone)
#
# Env:
#   NAMESPACE            hub install namespace (default hub)
#   EDGE_NAMESPACE       spoke edge namespace (default dark-noc-edge)
#   ACM_HUB_CLUSTER      hub ManagedCluster name (default local-cluster)
#   ARGOCD_NAMESPACE     optional; detected when unset (openshift-gitops|argocd)
#   ACM_DIR              default cross-cluster/acm
#   SKIP_OC_CHECK=1      offline skip of oc login (dry-run still works)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib-spokes.sh"

CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
NAMESPACE="${NAMESPACE:-hub}"
EDGE_NAMESPACE="${EDGE_NAMESPACE:-dark-noc-edge}"
ACM_HUB_CLUSTER="${ACM_HUB_CLUSTER:-local-cluster}"
ARGOCD_NAMESPACE="${ARGOCD_NAMESPACE:-}"
ACM_DIR="${ACM_DIR:-cross-cluster/acm}"
SKIP_OC_CHECK="${SKIP_OC_CHECK:-}"
STEP="all"

DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    --step=placement|--step=remaining|--step=all) STEP="${arg#--step=}" ;;
    -h|--help)
      sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
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

sed_escape() {
  printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g'
}

if ! [[ "${CLUSTER_COUNT}" =~ ^[0-9]+$ ]] || [[ "${CLUSTER_COUNT}" -lt 1 ]]; then
  fail "CLUSTER_COUNT must be an integer >= 1 (got: ${CLUSTER_COUNT})"
fi

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  log "SKIP: apply-placement (single-cluster mode, CLUSTER_COUNT=1)"
  exit 0
fi

for f in placement.yaml gitopscluster.yaml namespace-policy.yaml; do
  if [[ ! -f "${ACM_DIR}/${f}" ]]; then
    fail "missing ${ACM_DIR}/${f}"
  fi
done

detect_argocd_namespace() {
  local oc_bin="$1"
  if [[ -n "${ARGOCD_NAMESPACE}" ]]; then
    printf '%s' "${ARGOCD_NAMESPACE}"
    return 0
  fi
  if [[ "${DRY_RUN}" -eq 1 ]] || [[ -z "${oc_bin}" ]]; then
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
  printf '%s' "openshift-gitops"
}

oc_bin=""
skip_raw="$(printf '%s' "${SKIP_OC_CHECK}" | tr '[:upper:]' '[:lower:]')"
case "${skip_raw}" in
  1|true|yes)
    log "WARN: SKIP_OC_CHECK set; skipping oc login"
    ;;
  *)
    if [[ "${DRY_RUN}" -eq 0 ]]; then
      oc_bin="$(adnr_resolve_oc)"
      adnr_require_hub_login "${oc_bin}"
    else
      if command -v oc >/dev/null 2>&1 || command -v kubectl >/dev/null 2>&1; then
        oc_bin="$(adnr_resolve_oc)"
      fi
    fi
    ;;
esac

argocd_ns="$(detect_argocd_namespace "${oc_bin}")"
ns_esc="$(sed_escape "${NAMESPACE}")"
edge_esc="$(sed_escape "${EDGE_NAMESPACE}")"
hub_esc="$(sed_escape "${ACM_HUB_CLUSTER}")"
argo_esc="$(sed_escape "${argocd_ns}")"

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

substitute_file() {
  local src="$1" dest="$2"
  sed \
    -e "s|__NAMESPACE__|${ns_esc}|g" \
    -e "s|__EDGE_NAMESPACE__|${edge_esc}|g" \
    -e "s|__ACM_HUB_CLUSTER__|${hub_esc}|g" \
    -e "s|__ARGOCD_NAMESPACE__|${argo_esc}|g" \
    "${src}" > "${dest}"
}

files_for_step() {
  case "${STEP}" in
    placement) printf '%s\n' placement.yaml ;;
    remaining) printf '%s\n' gitopscluster.yaml namespace-policy.yaml ;;
    all) printf '%s\n' placement.yaml gitopscluster.yaml namespace-policy.yaml ;;
    *) fail "unknown step: ${STEP}" ;;
  esac
}

while IFS= read -r f; do
  [[ -n "${f}" ]] || continue
  substitute_file "${ACM_DIR}/${f}" "${tmpdir}/${f}"
done < <(files_for_step)

if [[ "${STEP}" == "remaining" || "${STEP}" == "all" ]]; then
  adnr_ensure_gitops_argo_namespace_label "${oc_bin}" "${argocd_ns}" "${NAMESPACE}" "${DRY_RUN}"
fi

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "=== dry-run: rendered ACM manifests (step=${STEP}) ==="
  while IFS= read -r f; do
    [[ -n "${f}" ]] || continue
    printf '\n# --- %s ---\n' "${f}"
    cat "${tmpdir}/${f}"
  done < <(files_for_step)
  log "OK: apply-placement dry-run (step=${STEP}, NAMESPACE=${NAMESPACE}, EDGE_NAMESPACE=${EDGE_NAMESPACE}, ACM_HUB_CLUSTER=${ACM_HUB_CLUSTER}, ARGOCD_NAMESPACE=${argocd_ns})"
  exit 0
fi

if [[ -z "${oc_bin}" ]]; then
  oc_bin="$(adnr_resolve_oc)"
fi

log "Applying ACM manifests (step=${STEP}, namespace=${NAMESPACE})..."
while IFS= read -r f; do
  [[ -n "${f}" ]] || continue
  "${oc_bin}" apply -f "${tmpdir}/${f}"
done < <(files_for_step)
log "OK: apply-placement complete (step=${STEP})"
exit 0
