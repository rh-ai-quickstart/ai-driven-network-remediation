#!/usr/bin/env bash
# Redeploy AI Driven Network Remediation on OpenShift (namespace fixed).
# Run from repository root: .cursor/skills/helm-redeploy/scripts/redeploy.sh

set -o errexit
set -o pipefail

readonly NS="ai-driven-network-remediation-itay"
readonly PULL_SECRET="quay-ikatav-pull"
readonly UPSTREAM_REGISTRY="quay.io/rh-ai-quickstart"

REGISTRY="${REGISTRY:-quay.io/ikatav}"
VERSION="${VERSION:-0.1.0}"
CONTAINER_TOOL="${CONTAINER_TOOL:-podman}"
# Default: full redeploy (uninstall then install). Set SKIP_UNINSTALL=true to install-only.
SKIP_UNINSTALL="${SKIP_UNINSTALL:-false}"
# Default: build/push from source when using a personal registry. Set SKIP_BUILD=true to skip.
SKIP_BUILD="${SKIP_BUILD:-false}"
# Rebuild all images even if they already exist in the registry (same tag, new code).
FORCE_BUILD="${FORCE_BUILD:-false}"
export SKIP_UNINSTALL SKIP_BUILD FORCE_BUILD CONTAINER_TOOL

readonly HUB_IMAGES=(
  noc-chatbot-service
  noc-ingestion-pipeline
  noc-agent-service
  noc-mcp-openshift
  noc-mcp-lokistack
  noc-mcp-kafka
  noc-mcp-aap
  noc-mcp-slack
  noc-mcp-servicenow
)
readonly MOCK_IMAGES=(aap-mock servicenow-mock)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

log() { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

make_env() {
  export NAMESPACE="${NS}"
  export EDGE_NAMESPACE="${NS}"
  export REGISTRY
  export VERSION
  export CONTAINER_TOOL
}

image_exists_in_registry() {
  local img=$1
  command -v skopeo >/dev/null || return 1
  skopeo inspect "docker://${REGISTRY}/${img}:${VERSION}" >/dev/null 2>&1
}

should_build_images() {
  case "${SKIP_BUILD}" in
    true|1|yes|YES|True)
      log "Skipping image build (SKIP_BUILD=${SKIP_BUILD})"
      return 1
      ;;
  esac

  if [[ "${REGISTRY}" == "${UPSTREAM_REGISTRY}" ]]; then
    log "Using upstream registry ${UPSTREAM_REGISTRY}; skipping local build (mirror on install failure)"
    return 1
  fi

  if [[ "${FORCE_BUILD}" == "true" ]]; then
    log "FORCE_BUILD=true; rebuilding all images"
    return 0
  fi

  local missing=()
  local img
  for img in "${HUB_IMAGES[@]}" "${MOCK_IMAGES[@]}"; do
    if ! image_exists_in_registry "${img}"; then
      missing+=("${img}")
    fi
  done

  if ((${#missing[@]} > 0)); then
    log "Missing from ${REGISTRY}: ${missing[*]}"
    return 0
  fi

  log "All images present in ${REGISTRY}; skipping build (set FORCE_BUILD=true after code changes)"
  return 1
}

build_and_push_images() {
  should_build_images || return 0

  command -v "${CONTAINER_TOOL}" >/dev/null || \
    die "${CONTAINER_TOOL} not found (required to build images)"

  make_env
  cd "${REPO_ROOT}"

  log "Building hub, MCP, and mock images (${REGISTRY}:${VERSION})"
  make build-all-images
  make push-all-images
  make build-push-aap-mock
  make build-push-servicenow-mock
  log "Images pushed to ${REGISTRY}"
}

ensure_prerequisites() {
  command -v oc >/dev/null || die "oc not found"
  command -v helm >/dev/null || die "helm not found"
  command -v make >/dev/null || die "make not found"
  oc whoami >/dev/null || die "not logged in to OpenShift (run oc login)"
  [[ -f "${REPO_ROOT}/.env" ]] || die ".env not found at ${REPO_ROOT}/.env"
  # shellcheck disable=SC1091
  set -a && source "${REPO_ROOT}/.env" && set +a
  [[ -n "${ADNR_LLM_ID:-}" && -n "${ADNR_LLM_URL:-}" && -n "${ADNR_LLM_TOKEN:-}" ]] || \
    die ".env must define ADNR_LLM_ID, ADNR_LLM_URL, ADNR_LLM_TOKEN"
}

ensure_namespace() {
  log "Using namespace ${NS}"
  oc create namespace "${NS}" 2>/dev/null || true
  oc project "${NS}"
}

ensure_pull_secret() {
  if oc get secret "${PULL_SECRET}" -n "${NS}" >/dev/null 2>&1; then
    log "Pull secret ${PULL_SECRET} already exists"
  else
    local auth_json="${HOME}/.config/containers/auth.json"
    [[ -f "${auth_json}" ]] || die "No ${PULL_SECRET} and no ${auth_json} to create one"
    log "Creating pull secret ${PULL_SECRET} from podman auth"
    python3 - "${auth_json}" "${PULL_SECRET}" "${NS}" <<'PY'
import base64, json, subprocess, sys
auth_path, secret_name, namespace = sys.argv[1:4]
with open(auth_path) as f:
    auth = json.load(f)["auths"]["quay.io"]["auth"]
user, password = base64.b64decode(auth).decode().split(":", 1)
yaml = subprocess.check_output([
    "oc", "create", "secret", "docker-registry", secret_name,
    "--docker-server=quay.io",
    f"--docker-username={user}",
    f"--docker-password={password}",
    f"--docker-email={user}@redhat.com",
    "-n", namespace,
    "--dry-run=client", "-o", "yaml",
])
subprocess.run(["oc", "apply", "-f", "-"], input=yaml, check=True)
PY
  fi
  for sa in default builder deployer; do
    oc secrets link "${sa}" "${PULL_SECRET}" --for=pull -n "${NS}" 2>/dev/null || true
  done
}

mirror_public_images() {
  command -v skopeo >/dev/null || { warn "skopeo not found; skipping image mirror"; return 0; }
  local images=(
    noc-chatbot-service
    noc-ingestion-pipeline
    noc-mcp-openshift
    noc-mcp-lokistack
    noc-mcp-kafka
    noc-mcp-aap
    noc-mcp-servicenow
    noc-mcp-slack
  )
  for img in "${images[@]}"; do
    log "Mirroring ${img}:${VERSION} to ${REGISTRY}"
    skopeo copy --all \
      "docker://${UPSTREAM_REGISTRY}/${img}:${VERSION}" \
      "docker://${REGISTRY}/${img}:${VERSION}" || warn "mirror failed for ${img}"
  done
}

build_missing_images() {
  log "Install failed; building and pushing images to ${REGISTRY}"
  SKIP_BUILD=false FORCE_BUILD=true build_and_push_images
}

patch_mock_pull_secrets() {
  for dep in aap-mock servicenow-mock; do
    if ! oc get deployment "${dep}" -n "${NS}" >/dev/null 2>&1; then
      continue
    fi
    log "Patching ${dep} with imagePullSecrets"
    oc patch deployment "${dep}" -n "${NS}" \
      -p "{\"spec\":{\"template\":{\"spec\":{\"imagePullSecrets\":[{\"name\":\"${PULL_SECRET}\"}]}}}}" \
      >/dev/null || true
    oc set image -n "${NS}" "deployment/${dep}" \
      "${dep}=${REGISTRY}/${dep}:${VERSION}" >/dev/null 2>&1 || true
    oc rollout restart "deployment/${dep}" -n "${NS}" >/dev/null || true
    oc rollout status "deployment/${dep}" -n "${NS}" --timeout=120s || warn "${dep} rollout not ready"
  done
}

fix_stale_pvc() {
  log "Removing stale pgvector PVC if present"
  oc delete pvc pg-data-pgvector-0 -n "${NS}" --ignore-not-found
}

run_helm_uninstall() {
  log "Running make helm-uninstall"
  make_env
  cd "${REPO_ROOT}"
  make helm-uninstall
}

run_helm_install() {
  log "Running make helm-install"
  make_env
  cd "${REPO_ROOT}"
  make helm-install
}

install_with_fallback() {
  if run_helm_install; then
    return 0
  fi
  warn "helm-install failed; running fallbacks"
  ensure_pull_secret
  mirror_public_images
  build_missing_images
  patch_mock_pull_secrets
  fix_stale_pvc
  log "Retrying make helm-install"
  run_helm_install
}

verify_deployment() {
  log "Verifying deployment"
  helm list -n "${NS}"
  echo "---"
  oc get pods -n "${NS}"
  echo "---"

  local not_ready
  not_ready="$(oc get pods -n "${NS}" --no-headers 2>/dev/null | \
    awk '$2 != "1/1" && $3 != "Completed" {print $1 " (" $2 " " $3 ")"}')"

  if [[ -n "${not_ready}" ]]; then
    warn "Some pods are not ready:"
    echo "${not_ready}"
    return 1
  fi

  local releases
  releases="$(helm list -n "${NS}" -q | sort | tr '\n' ' ')"
  echo "${releases}" | grep -qw "hub" || die "missing helm release: hub"

  log "Deployment complete in ${NS}"
  oc get routes -n "${NS}" 2>/dev/null || true
}

main() {
  cd "${REPO_ROOT}"
  ensure_prerequisites
  ensure_namespace
  ensure_pull_secret
  build_and_push_images

  case "${SKIP_UNINSTALL}" in
    true|1|yes|YES|True)
      log "Skipping uninstall (SKIP_UNINSTALL=${SKIP_UNINSTALL})"
      ;;
    *)
      run_helm_uninstall
      ;;
  esac

  install_with_fallback
  verify_deployment
}

main "$@"
