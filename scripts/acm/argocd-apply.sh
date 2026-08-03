#!/usr/bin/env bash
# Render and apply ADNR edge AppProject + ApplicationSet on the hub.
#
# CLUSTER_COUNT=1  → skip (single-cluster mode), exit 0
# CLUSTER_COUNT>=2 → render list elements from spokes.generated.yaml and apply
# --dry-run        → print rendered manifests; do not apply
#
# Env:
#   SPOKES_GENERATED     default hub/helm/spokes.generated.yaml
#   GITOPS_REPO_URL      required (source repo for edge/helm)
#   GITOPS_REVISION      required (branch/tag/commit)
#   EDGE_NAMESPACE       default dark-noc-edge
#   KAFKA_EXTERNAL_HOST  required for live apply; optional for --dry-run (C7 sets this)
#   ARGOCD_NAMESPACE     optional override (else detect openshift-gitops|argocd)
#   ARGOCD_DIR           default cross-cluster/argocd
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib-spokes.sh"

CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
SPOKES_GENERATED="${SPOKES_GENERATED:-hub/helm/spokes.generated.yaml}"
GITOPS_REPO_URL="${GITOPS_REPO_URL:-}"
GITOPS_REVISION="${GITOPS_REVISION:-}"
EDGE_NAMESPACE="${EDGE_NAMESPACE:-dark-noc-edge}"
KAFKA_EXTERNAL_HOST="${KAFKA_EXTERNAL_HOST:-}"
ARGOCD_NAMESPACE="${ARGOCD_NAMESPACE:-}"
ARGOCD_DIR="${ARGOCD_DIR:-cross-cluster/argocd}"
PROJECT_TEMPLATE="${ARGOCD_DIR}/project.yaml"
APPSET_TEMPLATE="${ARGOCD_DIR}/applicationset-edge.yaml"

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

# Escape sed replacement specials: \ & and the | delimiter we use.
sed_escape() {
  printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g'
}

if ! [[ "${CLUSTER_COUNT}" =~ ^[0-9]+$ ]] || [[ "${CLUSTER_COUNT}" -lt 1 ]]; then
  fail "CLUSTER_COUNT must be an integer >= 1 (got: ${CLUSTER_COUNT})"
fi

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  log "SKIP: argocd-apply (single-cluster mode, CLUSTER_COUNT=1)"
  exit 0
fi

adnr_require_spokes_file

if [[ ! -f "${PROJECT_TEMPLATE}" || ! -f "${APPSET_TEMPLATE}" ]]; then
  fail "missing ArgoCD templates under ${ARGOCD_DIR}/"
fi

if [[ -z "${GITOPS_REPO_URL}" ]]; then
  fail "GITOPS_REPO_URL is required when CLUSTER_COUNT>=2"
fi

if [[ -z "${GITOPS_REVISION}" ]]; then
  fail "GITOPS_REVISION is required when CLUSTER_COUNT>=2"
fi

# Parse "name|siteId|namespace" triples from topology.spokes
spokes=()
while IFS= read -r _line; do
  [[ -n "${_line}" ]] && spokes+=("${_line}")
done < <(adnr_spoke_triples)

if [[ "${#spokes[@]}" -eq 0 ]]; then
  fail "no spokes listed in ${SPOKES_GENERATED} (expected CLUSTER_COUNT=${CLUSTER_COUNT})"
fi

if [[ "${#spokes[@]}" -ne "${CLUSTER_COUNT}" ]]; then
  fail "spoke count mismatch: file has ${#spokes[@]}, CLUSTER_COUNT=${CLUSTER_COUNT}"
fi

# Build list-generator elements YAML (10-space indent under elements:).
elements=""
spoke_names=()
for entry in "${spokes[@]}"; do
  name="${entry%%|*}"
  rest="${entry#*|}"
  site="${rest%%|*}"
  ns="${rest#*|}"
  if [[ "${ns}" == "${rest}" ]]; then
    ns="${EDGE_NAMESPACE}"
  fi
  spoke_names+=("${name}")
  elements+="          - name: ${name}"$'\n'
  elements+="            siteId: ${site}"$'\n'
  elements+="            namespace: ${ns}"$'\n'
done

detect_argocd_namespace() {
  local oc_bin="$1"
  if [[ -n "${ARGOCD_NAMESPACE}" ]]; then
    printf '%s' "${ARGOCD_NAMESPACE}"
    return 0
  fi
  if [[ -n "${oc_bin}" ]] && "${oc_bin}" get namespace openshift-gitops >/dev/null 2>&1; then
    printf '%s' "openshift-gitops"
    return 0
  fi
  if [[ -n "${oc_bin}" ]] && "${oc_bin}" get namespace argocd >/dev/null 2>&1; then
    printf '%s' "argocd"
    return 0
  fi
  # Dry-run / offline default matches OpenShift GitOps.
  printf '%s' "openshift-gitops"
}

oc_bin=""
if command -v oc >/dev/null 2>&1; then
  oc_bin=oc
elif command -v kubectl >/dev/null 2>&1; then
  oc_bin=kubectl
fi

argocd_ns="$(detect_argocd_namespace "${oc_bin:-}")"

# Sample tokens baked into committed manifests (valid for client dry-run).
SAMPLE_ARGOCD_NS="openshift-gitops"
SAMPLE_EDGE_NS="dark-noc-edge"
SAMPLE_REPO_URL="https://github.com/rh-ai-quickstart/ai-driven-network-remediation.git"
SAMPLE_REVISION="main"
SAMPLE_KAFKA_HOST="__KAFKA_EXTERNAL_HOST__"

repo_esc="$(sed_escape "${GITOPS_REPO_URL}")"
rev_esc="$(sed_escape "${GITOPS_REVISION}")"
ns_esc="$(sed_escape "${argocd_ns}")"
edge_ns_esc="$(sed_escape "${EDGE_NAMESPACE}")"
kafka_esc="$(sed_escape "${KAFKA_EXTERNAL_HOST}")"
sample_repo_esc="$(sed_escape "${SAMPLE_REPO_URL}")"
sample_rev_esc="$(sed_escape "${SAMPLE_REVISION}")"
sample_ns_esc="$(sed_escape "${SAMPLE_ARGOCD_NS}")"
sample_edge_esc="$(sed_escape "${SAMPLE_EDGE_NS}")"
sample_kafka_esc="$(sed_escape "${SAMPLE_KAFKA_HOST}")"

substitute_common() {
  sed \
    -e "s|${sample_repo_esc}|${repo_esc}|g" \
    -e "s|targetRevision: ${sample_rev_esc}|targetRevision: ${rev_esc}|g" \
    -e "s|namespace: ${sample_ns_esc}|namespace: ${ns_esc}|g" \
    -e "s|namespace: ${sample_edge_esc}|namespace: ${edge_ns_esc}|g" \
    -e "s|${sample_kafka_esc}|${kafka_esc}|g"
}

render_project() {
  substitute_common < "${PROJECT_TEMPLATE}"
}

render_appset() {
  local elements_file="$1"
  # Replace the sample spoke block with topology-derived elements.
  awk -v elements_file="${elements_file}" '
    BEGIN { skipping = 0 }
    /# SPOKE_ELEMENTS_START/ {
      print
      while ((getline line < elements_file) > 0) print line
      close(elements_file)
      skipping = 1
      next
    }
    /# SPOKE_ELEMENTS_END/ {
      skipping = 0
      print
      next
    }
    skipping { next }
    { print }
  ' "${APPSET_TEMPLATE}" | substitute_common
}

elements_file="$(mktemp)"
trap 'rm -f "${elements_file}"' EXIT
printf '%s' "${elements}" > "${elements_file}"

rendered="$(render_project)"
rendered+=$'\n'
rendered+="$(render_appset "${elements_file}")"

log "spokes: ${spoke_names[*]}"
log "argocdNamespace: ${argocd_ns}"
log "gitops: ${GITOPS_REPO_URL}@${GITOPS_REVISION}"
log "edge path: edge/helm  kafka.externalHost: ${KAFKA_EXTERNAL_HOST:-<empty>}"

# Count list-generator elements between markers (prefix-agnostic; supports SPOKE_NAME_PREFIX).
count_list_elements() {
  printf '%s\n' "$1" | awk '
    /# SPOKE_ELEMENTS_START/ { in_block = 1; next }
    /# SPOKE_ELEMENTS_END/ { in_block = 0; next }
    in_block && /^[[:space:]]*- name:[[:space:]]+/ { count++ }
    END { print count + 0 }
  '
}

if [[ "${DRY_RUN}" -eq 1 ]]; then
  if [[ -z "${KAFKA_EXTERNAL_HOST}" ]]; then
    log "WARN: KAFKA_EXTERNAL_HOST unset; rendered kafka.externalHost is empty (edge chart will fail sync until set)"
  fi
  log "--- dry-run manifests ---"
  printf '%s\n' "${rendered}"
  appset_count="$(printf '%s\n' "${rendered}" | grep -c '^kind: ApplicationSet$' || true)"
  project_count="$(printf '%s\n' "${rendered}" | grep -c '^kind: AppProject$' || true)"
  element_count="$(count_list_elements "${rendered}")"
  if [[ "${appset_count}" -ne 1 || "${project_count}" -ne 1 ]]; then
    fail "dry-run expected 1 AppProject and 1 ApplicationSet (got project=${project_count} appset=${appset_count})"
  fi
  if [[ "${element_count}" -ne "${#spokes[@]}" ]]; then
    fail "dry-run expected ${#spokes[@]} list elements, found ${element_count}"
  fi
  log "OK: argocd-apply dry-run rendered AppProject + ApplicationSet for ${#spokes[@]} spoke(s)"
  exit 0
fi

if [[ -z "${KAFKA_EXTERNAL_HOST}" ]]; then
  fail "KAFKA_EXTERNAL_HOST is required for live apply (hub Kafka Route hostname). Example: export KAFKA_EXTERNAL_HOST=\$(oc get route kafka-external -n hub -o jsonpath='{.spec.host}')"
fi

if [[ -z "${oc_bin}" ]]; then
  fail "oc or kubectl not found on PATH"
fi

if ! "${oc_bin}" whoami >/dev/null 2>&1; then
  fail "not logged into hub cluster (${oc_bin} whoami failed)"
fi

if ! "${oc_bin}" get crd applicationsets.argoproj.io >/dev/null 2>&1; then
  fail "ArgoCD ApplicationSet CRD missing: applicationsets.argoproj.io (is OpenShift GitOps installed?)"
fi

if ! "${oc_bin}" get namespace "${argocd_ns}" >/dev/null 2>&1; then
  fail "ArgoCD namespace not found: ${argocd_ns}"
fi

log "Applying AppProject + ApplicationSet to ${argocd_ns}..."
printf '%s\n' "${rendered}" | "${oc_bin}" apply -f -
log "OK: argocd-apply applied edge fan-out for ${#spokes[@]} spoke(s)"
log "NOTE: spokes must be registered as ArgoCD clusters (ACM GitOps). Missing cluster secrets → Applications stay Unknown; see C9."
exit 0
