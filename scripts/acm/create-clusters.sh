#!/usr/bin/env bash
# Render (and optionally apply) Hive ClusterDeployments for ADNR spokes.
#
# CLUSTER_CREATE=false (default) → skip, exit 0
# --dry-run                       → render manifests for all spokes; do not apply
#                                    (works even when CLUSTER_CREATE=false)
# CLUSTER_CREATE=true             → render and apply (requires Hive secrets + creds)
#
# Reads spoke names from hub/helm/spokes.generated.yaml (run validate-topology first).
#
# Env (apply path):
#   HIVE_BASE_DOMAIN          required for apply (e.g. example.com)
#   HIVE_CLUSTER_IMAGE_SET    ClusterImageSet name (default: img4.20.12-x86-64-appsub)
#   HIVE_AWS_REGION           AWS region (default: us-east-1)
#   CLUSTER_DEPLOYMENT_TEMPLATE  path to template YAML
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib-spokes.sh"

CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
CLUSTER_CREATE="${CLUSTER_CREATE:-false}"
SPOKES_GENERATED="${SPOKES_GENERATED:-hub/helm/spokes.generated.yaml}"
TEMPLATE="${CLUSTER_DEPLOYMENT_TEMPLATE:-cross-cluster/acm/clusterdeployment.yaml}"
HIVE_BASE_DOMAIN="${HIVE_BASE_DOMAIN:-}"
HIVE_CLUSTER_IMAGE_SET="${HIVE_CLUSTER_IMAGE_SET:-img4.20.12-x86-64-appsub}"
HIVE_AWS_REGION="${HIVE_AWS_REGION:-us-east-1}"

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

create_raw="$(printf '%s' "${CLUSTER_CREATE}" | tr '[:upper:]' '[:lower:]')"
create_enabled=0
case "${create_raw}" in
  1|true|yes) create_enabled=1 ;;
esac

if ! [[ "${CLUSTER_COUNT}" =~ ^[0-9]+$ ]] || [[ "${CLUSTER_COUNT}" -lt 1 ]]; then
  fail "CLUSTER_COUNT must be an integer >= 1 (got: ${CLUSTER_COUNT})"
fi

# Skip when not creating and not dry-running.
if [[ "${DRY_RUN}" -eq 0 && "${create_enabled}" -eq 0 ]]; then
  log "SKIP: acm-create-clusters (CLUSTER_CREATE=${CLUSTER_CREATE}; set true to provision via Hive)"
  exit 0
fi

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "SKIP: acm-create-clusters --dry-run (single-cluster mode, CLUSTER_COUNT=1; no spokes)"
    exit 0
  fi
  log "SKIP: acm-create-clusters (single-cluster mode, CLUSTER_COUNT=1; no spokes)"
  exit 0
fi

adnr_require_spokes_file

if [[ ! -f "${TEMPLATE}" ]]; then
  fail "missing ClusterDeployment template: ${TEMPLATE}"
fi

# Parse name|siteId pairs from topology.spokes
spokes=()
while IFS= read -r _line; do
  [[ -n "${_line}" ]] || continue
  name="${_line%%|*}"
  rest="${_line#*|}"
  site="${rest%%|*}"
  spokes+=("${name}|${site}")
done < <(adnr_spoke_triples)

if [[ "${#spokes[@]}" -eq 0 ]]; then
  fail "no spokes listed in ${SPOKES_GENERATED} (expected CLUSTER_COUNT=${CLUSTER_COUNT})"
fi

if [[ "${#spokes[@]}" -ne "${CLUSTER_COUNT}" ]]; then
  fail "spoke count mismatch: file has ${#spokes[@]}, CLUSTER_COUNT=${CLUSTER_COUNT}"
fi

# Dry-run may use a placeholder domain; apply requires a real one.
base_domain="${HIVE_BASE_DOMAIN}"
if [[ -z "${base_domain}" ]]; then
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    base_domain="example.com"
    log "WARN: HIVE_BASE_DOMAIN unset; using ${base_domain} for dry-run only"
  else
    fail "HIVE_BASE_DOMAIN is required when CLUSTER_CREATE=true (e.g. export HIVE_BASE_DOMAIN=lab.example.com)"
  fi
fi

render_spoke() {
  local spoke_name="$1"
  local site_id="$2"
  local name_esc site_esc domain_esc imageset_esc region_esc
  name_esc="$(sed_escape "${spoke_name}")"
  site_esc="$(sed_escape "${site_id}")"
  domain_esc="$(sed_escape "${base_domain}")"
  imageset_esc="$(sed_escape "${HIVE_CLUSTER_IMAGE_SET}")"
  region_esc="$(sed_escape "${HIVE_AWS_REGION}")"
  sed \
    -e "s|__SPOKE_NAME__|${name_esc}|g" \
    -e "s|__SITE_ID__|${site_esc}|g" \
    -e "s|__BASE_DOMAIN__|${domain_esc}|g" \
    -e "s|__IMAGE_SET__|${imageset_esc}|g" \
    -e "s|__AWS_REGION__|${region_esc}|g" \
    "${TEMPLATE}"
}

rendered=""
spoke_names=()
for entry in "${spokes[@]}"; do
  name="${entry%%|*}"
  site="${entry#*|}"
  spoke_names+=("${name}")
  doc="$(render_spoke "${name}" "${site}")"
  if [[ -n "${rendered}" ]]; then
    rendered+=$'\n'
  fi
  rendered+="${doc}"
done

log "spokes: ${spoke_names[*]}"
log "imageSet: ${HIVE_CLUSTER_IMAGE_SET}  region: ${HIVE_AWS_REGION}  baseDomain: ${base_domain}"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "--- dry-run manifests ---"
  printf '%s\n' "${rendered}"
  # Sanity: expect one ClusterDeployment per spoke
  cd_count="$(printf '%s\n' "${rendered}" | grep -c '^kind: ClusterDeployment$' || true)"
  if [[ "${cd_count}" -ne "${#spokes[@]}" ]]; then
    fail "dry-run expected ${#spokes[@]} ClusterDeployment(s), found ${cd_count}"
  fi
  log "OK: acm-create-clusters dry-run rendered ${cd_count} ClusterDeployment(s)"
  exit 0
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

if ! "${oc_bin}" get crd clusterdeployments.hive.openshift.io >/dev/null 2>&1; then
  fail "Hive CRD missing: clusterdeployments.hive.openshift.io (is ACM/Hive installed?)"
fi

log "Applying ClusterDeployment manifests for ${#spokes[@]} spoke(s)..."
printf '%s\n' "${rendered}" | "${oc_bin}" apply -f -
log "OK: acm-create-clusters applied ${#spokes[@]} ClusterDeployment(s)"
log "NOTE: ensure pull-secret, ssh-private-key, aws-creds, and install-config secrets exist in each spoke namespace"
log "NOTE: wait for ManagedClusters with scripts/acm/wait-for-clusters.sh (C7) or: oc get managedcluster"
exit 0
