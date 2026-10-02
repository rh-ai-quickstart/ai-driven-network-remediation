#!/usr/bin/env bash
# Render and apply ADNR edge AppProject + ApplicationSet on the hub.
#
# CLUSTER_COUNT=1  → one list element (destination in-cluster, siteId=edge-site-01)
# CLUSTER_COUNT>=2 → list elements from spokes.generated.yaml (ManagedCluster names)
# --dry-run        → print rendered manifests; do not apply
#
# Env:
#   SPOKES_GENERATED              default hub/helm/spokes.generated.yaml (required for N>=2)
#   GITOPS_REPO_URL               required (source repo for edge/helm)
#   GITOPS_REVISION               required (branch/tag/commit)
#   EDGE_NAMESPACE                default dark-noc-edge
#   KAFKA_EXTERNAL_HOST           required for live apply when CLF enabled; optional for --dry-run
#   ARGOCD_NAMESPACE              optional override (else detect openshift-gitops|argocd)
#   ARGOCD_DIR                    default cross-cluster/argocd
#   EDGE_SELF_HEAL                default true (ApplicationSet syncPolicy.automated.selfHeal)
#   REGISTRY                      default quay.io/rh-ai-quickstart (healer image repo prefix)
#   VERSION                       default 0.1.5 (healer image tag)
#   CLUSTER_LOG_FORWARDER_ENABLED default false (N=1) / true (N>=2)
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
EDGE_SELF_HEAL="${EDGE_SELF_HEAL:-true}"
REGISTRY="${REGISTRY:-quay.io/rh-ai-quickstart}"
VERSION="${VERSION:-0.1.5}"
PROJECT_TEMPLATE="${ARGOCD_DIR}/project.yaml"
APPSET_TEMPLATE="${ARGOCD_DIR}/applicationset-edge.yaml"

# Topology defaults: SC keeps CLF off (no Logging/certs prereq); MC keeps CLF on.
if [[ -z "${CLUSTER_LOG_FORWARDER_ENABLED:-}" ]]; then
  if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
    CLUSTER_LOG_FORWARDER_ENABLED="false"
  else
    CLUSTER_LOG_FORWARDER_ENABLED="true"
  fi
fi

DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
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

# Escape sed replacement specials: \ & and the | delimiter we use.
sed_escape() {
  printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g'
}

if ! [[ "${CLUSTER_COUNT}" =~ ^[0-9]+$ ]] || [[ "${CLUSTER_COUNT}" -lt 1 ]]; then
  fail "CLUSTER_COUNT must be an integer >= 1 (got: ${CLUSTER_COUNT})"
fi

if [[ ! -f "${PROJECT_TEMPLATE}" || ! -f "${APPSET_TEMPLATE}" ]]; then
  fail "missing ArgoCD templates under ${ARGOCD_DIR}/"
fi

if [[ -z "${GITOPS_REPO_URL}" ]]; then
  fail "GITOPS_REPO_URL is required"
fi

if [[ -z "${GITOPS_REVISION}" ]]; then
  fail "GITOPS_REVISION is required"
fi

FAST_PATH_HEALER_IMAGE_REPO="${REGISTRY}/noc-edge-fast-path-healer"
FAST_PATH_HEALER_IMAGE_TAG="${VERSION}"

# Build list-generator elements YAML (10-space indent under elements:).
elements=""
element_names=()
expected_element_count=0

append_element() {
  local name="$1"
  local site="$2"
  local ns="$3"
  local dest="$4"
  element_names+=("${name}")
  elements+="          - name: ${name}"$'\n'
  elements+="            siteId: ${site}"$'\n'
  elements+="            namespace: ${ns}"$'\n'
  elements+="            destinationName: ${dest}"$'\n'
}

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  # Local GitOps edge: destination is the hub cluster itself (not a ManagedCluster).
  # siteId stays edge-site-01 for demo/MCP aliases; hub topology.spokes stays [].
  append_element "in-cluster" "edge-site-01" "${EDGE_NAMESPACE}" "in-cluster"
  expected_element_count=1
  log "mode: single-cluster (destination in-cluster, siteId=edge-site-01)"
else
  adnr_require_spokes_file

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

  for entry in "${spokes[@]}"; do
    name="${entry%%|*}"
    rest="${entry#*|}"
    site="${rest%%|*}"
    ns="${rest#*|}"
    if [[ "${ns}" == "${rest}" ]]; then
      ns="${EDGE_NAMESPACE}"
    fi
    # ManagedCluster name = ArgoCD cluster secret name (ACM GitOps registers these).
    append_element "${name}" "${site}" "${ns}" "${name}"
  done
  expected_element_count="${#spokes[@]}"
  log "mode: hub-spoke (${expected_element_count} spoke destination(s))"
fi

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
SAMPLE_HEALER_REPO="__FAST_PATH_HEALER_IMAGE_REPO__"
SAMPLE_HEALER_TAG="__FAST_PATH_HEALER_IMAGE_TAG__"
SAMPLE_CLF_ENABLED="__CLUSTER_LOG_FORWARDER_ENABLED__"

repo_esc="$(sed_escape "${GITOPS_REPO_URL}")"
rev_esc="$(sed_escape "${GITOPS_REVISION}")"
ns_esc="$(sed_escape "${argocd_ns}")"
edge_ns_esc="$(sed_escape "${EDGE_NAMESPACE}")"
kafka_esc="$(sed_escape "${KAFKA_EXTERNAL_HOST}")"
healer_repo_esc="$(sed_escape "${FAST_PATH_HEALER_IMAGE_REPO}")"
healer_tag_esc="$(sed_escape "${FAST_PATH_HEALER_IMAGE_TAG}")"
clf_esc="$(sed_escape "${CLUSTER_LOG_FORWARDER_ENABLED}")"
sample_repo_esc="$(sed_escape "${SAMPLE_REPO_URL}")"
sample_rev_esc="$(sed_escape "${SAMPLE_REVISION}")"
sample_ns_esc="$(sed_escape "${SAMPLE_ARGOCD_NS}")"
sample_edge_esc="$(sed_escape "${SAMPLE_EDGE_NS}")"
sample_kafka_esc="$(sed_escape "${SAMPLE_KAFKA_HOST}")"
sample_healer_repo_esc="$(sed_escape "${SAMPLE_HEALER_REPO}")"
sample_healer_tag_esc="$(sed_escape "${SAMPLE_HEALER_TAG}")"
sample_clf_esc="$(sed_escape "${SAMPLE_CLF_ENABLED}")"

substitute_common() {
  sed \
    -e "s|${sample_repo_esc}|${repo_esc}|g" \
    -e "s|targetRevision: ${sample_rev_esc}|targetRevision: ${rev_esc}|g" \
    -e "s|namespace: ${sample_ns_esc}|namespace: ${ns_esc}|g" \
    -e "s|namespace: ${sample_edge_esc}|namespace: ${edge_ns_esc}|g" \
    -e "s|${sample_kafka_esc}|${kafka_esc}|g" \
    -e "s|${sample_healer_repo_esc}|${healer_repo_esc}|g" \
    -e "s|${sample_healer_tag_esc}|${healer_tag_esc}|g" \
    -e "s|${sample_clf_esc}|${clf_esc}|g" \
    -e "s|__EDGE_SELF_HEAL__|${EDGE_SELF_HEAL}|g"
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

log "elements: ${element_names[*]}"
log "argocdNamespace: ${argocd_ns}"
log "gitops: ${GITOPS_REPO_URL}@${GITOPS_REVISION}"
log "edge path: edge/helm  kafka.externalHost: ${KAFKA_EXTERNAL_HOST:-<empty>}"
log "healer image: ${FAST_PATH_HEALER_IMAGE_REPO}:${FAST_PATH_HEALER_IMAGE_TAG}"
log "clusterLogForwarder.enabled: ${CLUSTER_LOG_FORWARDER_ENABLED}  selfHeal: ${EDGE_SELF_HEAL}"

# Count list-generator elements between markers (prefix-agnostic; supports SPOKE_NAME_PREFIX).
count_list_elements() {
  printf '%s\n' "$1" | awk '
    /# SPOKE_ELEMENTS_START/ { in_block = 1; next }
    /# SPOKE_ELEMENTS_END/ { in_block = 0; next }
    in_block && /^[[:space:]]*- name:[[:space:]]+/ { count++ }
    END { print count + 0 }
  '
}

clf_enabled_true() {
  case "${CLUSTER_LOG_FORWARDER_ENABLED}" in
    true|TRUE|yes|YES|1) return 0 ;;
    *) return 1 ;;
  esac
}

if [[ "${DRY_RUN}" -eq 1 ]]; then
  if clf_enabled_true && [[ -z "${KAFKA_EXTERNAL_HOST}" ]]; then
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
  if [[ "${element_count}" -ne "${expected_element_count}" ]]; then
    fail "dry-run expected ${expected_element_count} list elements, found ${element_count}"
  fi
  if ! printf '%s\n' "${rendered}" | grep -qE "value: \"?${FAST_PATH_HEALER_IMAGE_TAG}\"?"; then
    fail "dry-run missing healer image tag ${FAST_PATH_HEALER_IMAGE_TAG}"
  fi
  if ! printf '%s\n' "${rendered}" | grep -q "selfHeal: ${EDGE_SELF_HEAL}"; then
    fail "dry-run missing selfHeal: ${EDGE_SELF_HEAL}"
  fi
  if ! printf '%s\n' "${rendered}" | grep -q "destinationName: ${element_names[0]}"; then
    fail "dry-run missing destinationName: ${element_names[0]}"
  fi
  log "OK: argocd-apply dry-run rendered AppProject + ApplicationSet for ${expected_element_count} element(s)"
  exit 0
fi

if clf_enabled_true && [[ -z "${KAFKA_EXTERNAL_HOST}" ]]; then
  fail "KAFKA_EXTERNAL_HOST is required when clusterLogForwarder.enabled=true (hub Kafka Route hostname). Example: export KAFKA_EXTERNAL_HOST=\$(oc get route kafka-external -n hub -o jsonpath='{.spec.host}')"
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

# OpenShift GitOps only grants the application-controller write access in namespaces
# labeled argocd.argoproj.io/managed-by=<gitops-ns>. CreateNamespace alone leaves
# Applications Healthy but OutOfSync (forbidden create on Deployments/Services).
ensure_in_cluster_edge_namespace_rbac() {
  local ns="${EDGE_NAMESPACE}"
  local managed_by="${argocd_ns}"
  local deadline
  log "Ensuring ${ns} exists and is managed by OpenShift GitOps (${managed_by})..."
  if ! "${oc_bin}" get namespace "${ns}" >/dev/null 2>&1; then
    "${oc_bin}" create namespace "${ns}"
  fi
  "${oc_bin}" label namespace "${ns}" "argocd.argoproj.io/managed-by=${managed_by}" --overwrite

  deadline=$((SECONDS + 120))
  while (( SECONDS < deadline )); do
    if "${oc_bin}" get rolebinding -n "${ns}" -o jsonpath='{range .items[*]}{.subjects[*].name}{"\n"}{end}' 2>/dev/null \
      | grep -q 'argocd-application-controller'; then
      log "OK: application-controller RoleBinding present in ${ns}"
      return 0
    fi
    sleep 3
  done
  log "WARN: timed out waiting for GitOps operator RoleBinding in ${ns}; applying admin RoleBinding fallback"
  # Fallback when the operator does not reconcile managed-by quickly (or custom installs).
  "${oc_bin}" create rolebinding "adnr-argocd-application-controller" \
    --namespace="${ns}" \
    --clusterrole=admin \
    --serviceaccount="${managed_by}:openshift-gitops-argocd-application-controller" \
    --dry-run=client -o yaml | "${oc_bin}" apply -f -
}

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  ensure_in_cluster_edge_namespace_rbac
fi

log "Applying AppProject + ApplicationSet to ${argocd_ns}..."
printf '%s\n' "${rendered}" | "${oc_bin}" apply -f -
log "OK: argocd-apply applied edge fan-out for ${expected_element_count} element(s)"
if [[ "${CLUSTER_COUNT}" -ge 2 ]]; then
  log "NOTE: spokes must be registered as ArgoCD clusters (ACM GitOps). Missing cluster secrets → Applications stay Unknown; see C9."
fi
exit 0
