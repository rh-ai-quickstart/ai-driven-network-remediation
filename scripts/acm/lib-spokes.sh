#!/usr/bin/env bash
# Shared helpers for ADNR ACM scripts (spoke list parse, oc binary).
# Source from other scripts:  # shellcheck source=scripts/acm/lib-spokes.sh
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   # shellcheck disable=SC1091
#   source "${SCRIPT_DIR}/lib-spokes.sh"
#
# Expects SPOKES_GENERATED (path to hub/helm/spokes.generated.yaml).

adnr_log() { printf '%s\n' "$*"; }
adnr_fail() { adnr_log "ERROR: $*"; exit 1; }

adnr_require_spokes_file() {
  local path="${SPOKES_GENERATED:-hub/helm/spokes.generated.yaml}"
  if [[ ! -f "${path}" ]]; then
    adnr_fail "missing ${path}; run 'make validate-topology' first"
  fi
}

# Print ManagedCluster names, one per line (topology.spokes only).
adnr_spoke_names() {
  local path="${SPOKES_GENERATED:-hub/helm/spokes.generated.yaml}"
  awk '
    /^[[:space:]]*spokes:[[:space:]]*\[\][[:space:]]*$/ { exit }
    /^[[:space:]]*spokes:[[:space:]]*$/ { in_spokes = 1; next }
    in_spokes && /^[^[:space:]#]/ { exit }
    in_spokes && /^[[:space:]]*- name:[[:space:]]+/ {
      name = $0
      sub(/^[[:space:]]*- name:[[:space:]]+/, "", name)
      gsub(/[[:space:]]+$/, "", name)
      if (name != "") print name
    }
  ' "${path}"
}

# Print name|siteId|namespace triples, one per line (topology.spokes only).
adnr_spoke_triples() {
  local path="${SPOKES_GENERATED:-hub/helm/spokes.generated.yaml}"
  local default_ns="${EDGE_NAMESPACE:-dark-noc-edge}"
  awk -v default_ns="${default_ns}" '
    function flush() {
      if (name != "") {
        if (site == "") site = "unknown"
        if (ns == "") ns = default_ns
        print name "|" site "|" ns
        name = ""
        site = ""
        ns = ""
      }
    }
    /^[[:space:]]*spokes:[[:space:]]*\[\][[:space:]]*$/ { exit }
    /^[[:space:]]*spokes:[[:space:]]*$/ { in_spokes = 1; next }
    in_spokes && /^[^[:space:]#]/ { flush(); exit }
    in_spokes && /^[[:space:]]*- name:[[:space:]]+/ {
      flush()
      name = $0
      sub(/^[[:space:]]*- name:[[:space:]]+/, "", name)
      gsub(/[[:space:]]+$/, "", name)
      site = ""
      ns = ""
      next
    }
    in_spokes && /^[[:space:]]*siteId:[[:space:]]+/ {
      site = $0
      sub(/^[[:space:]]*siteId:[[:space:]]+/, "", site)
      gsub(/[[:space:]]+$/, "", site)
      next
    }
    in_spokes && /^[[:space:]]*namespace:[[:space:]]+/ {
      ns = $0
      sub(/^[[:space:]]*namespace:[[:space:]]+/, "", ns)
      gsub(/[[:space:]]+$/, "", ns)
      next
    }
    END { flush() }
  ' "${path}"
}

# Append spoke names into global array ADNR_SPOKE_NAMES (bash 3.2 compatible).
adnr_load_spoke_names() {
  local _spoke
  ADNR_SPOKE_NAMES=()
  while IFS= read -r _spoke; do
    [[ -n "${_spoke}" ]] && ADNR_SPOKE_NAMES+=("${_spoke}")
  done < <(adnr_spoke_names)
}

adnr_resolve_oc() {
  if command -v oc >/dev/null 2>&1; then
    printf '%s' "oc"
  elif command -v kubectl >/dev/null 2>&1; then
    printf '%s' "kubectl"
  else
    adnr_fail "oc or kubectl not found on PATH"
  fi
}

adnr_require_hub_login() {
  local oc_bin="${1:-}"
  if [[ -z "${oc_bin}" ]]; then
    oc_bin="$(adnr_resolve_oc)"
  fi
  if ! "${oc_bin}" whoami >/dev/null 2>&1; then
    adnr_fail "not logged into hub cluster (${oc_bin} whoami failed)"
  fi
}
