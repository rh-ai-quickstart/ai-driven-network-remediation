#!/usr/bin/env bash
# Distribute hub Kafka client TLS certs to each spoke (kafka-client-certs secret).
#
# Automates hub/helm/charts/kafka/README.md steps 1–2 for ACM spokes:
#   1. Read ca.crt / client.crt / client.key from hub secret kafka-client-tls
#      (in-memory / temp dir only; does not write certs into the repo cwd)
#   2. Create kafka-client-certs in EDGE_NAMESPACE on each spoke
#
# Spoke access order per ManagedCluster:
#   a) Hive/import admin kubeconfig (${spoke}-admin-kubeconfig / ${spoke}-kubeconfig)
#   b) ManifestWork on the ManagedCluster namespace (hub-only; no spoke API from laptop)
#      Waits for ManifestWork Applied=True before returning (avoids ArgoCD/CLF race).
#
# CLUSTER_COUNT=1  → skip, exit 0
# --dry-run        → print plan; do not write secrets
#
# Env:
#   SPOKES_GENERATED   default hub/helm/spokes.generated.yaml
#   NAMESPACE          hub namespace (default hub) — source of kafka-client-tls
#   EDGE_NAMESPACE     spoke target namespace (default dark-noc-edge)
#   ACM_MANIFESTWORK_TIMEOUT_SECONDS   default 300
#   ACM_MANIFESTWORK_INTERVAL_SECONDS  default 5
#   SKIP_OC_CHECK=1    offline skip
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib-spokes.sh"

CLUSTER_COUNT="${CLUSTER_COUNT:-1}"
SPOKES_GENERATED="${SPOKES_GENERATED:-hub/helm/spokes.generated.yaml}"
NAMESPACE="${NAMESPACE:-hub}"
EDGE_NAMESPACE="${EDGE_NAMESPACE:-dark-noc-edge}"
SKIP_OC_CHECK="${SKIP_OC_CHECK:-}"
SECRET_NAME="kafka-client-certs"
SOURCE_SECRET="kafka-client-tls"
MANIFESTWORK_NAME="adnr-kafka-client-certs"
MW_TIMEOUT_SECONDS="${ACM_MANIFESTWORK_TIMEOUT_SECONDS:-300}"
MW_INTERVAL_SECONDS="${ACM_MANIFESTWORK_INTERVAL_SECONDS:-5}"

DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
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

if ! [[ "${CLUSTER_COUNT}" =~ ^[0-9]+$ ]] || [[ "${CLUSTER_COUNT}" -lt 1 ]]; then
  fail "CLUSTER_COUNT must be an integer >= 1 (got: ${CLUSTER_COUNT})"
fi

if [[ "${CLUSTER_COUNT}" -eq 1 ]]; then
  log "SKIP: distribute-kafka-certs (single-cluster mode, CLUSTER_COUNT=1)"
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
    log "WARN: SKIP_OC_CHECK set; would distribute ${SECRET_NAME} to: ${ADNR_SPOKE_NAMES[*]} (ns=${EDGE_NAMESPACE})"
    log "OK: distribute-kafka-certs skipped live oc (CLUSTER_COUNT=${CLUSTER_COUNT})"
    exit 0
    ;;
esac

oc_bin="$(adnr_resolve_oc)"
adnr_require_hub_login "${oc_bin}"

if ! "${oc_bin}" get secret "${SOURCE_SECRET}" -n "${NAMESPACE}" >/dev/null 2>&1; then
  fail "hub secret ${SOURCE_SECRET} not found in ${NAMESPACE}; run helm-install / make kafka-client-cert first"
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

"${oc_bin}" get secret "${SOURCE_SECRET}" -n "${NAMESPACE}" \
  -o jsonpath='{.data.ca\.crt}' | base64 -d > "${tmpdir}/ca.crt"
"${oc_bin}" get secret "${SOURCE_SECRET}" -n "${NAMESPACE}" \
  -o jsonpath='{.data.client\.crt}' | base64 -d > "${tmpdir}/client.crt"
"${oc_bin}" get secret "${SOURCE_SECRET}" -n "${NAMESPACE}" \
  -o jsonpath='{.data.client\.key}' | base64 -d > "${tmpdir}/client.key"

for f in ca.crt client.crt client.key; do
  if [[ ! -s "${tmpdir}/${f}" ]]; then
    fail "failed to extract ${f} from ${SOURCE_SECRET}"
  fi
done

ca_b64="$(base64 < "${tmpdir}/ca.crt" | tr -d '\n')"
crt_b64="$(base64 < "${tmpdir}/client.crt" | tr -d '\n')"
key_b64="$(base64 < "${tmpdir}/client.key" | tr -d '\n')"

extract_hive_kubeconfig() {
  local spoke="$1" out="$2" secret
  for secret in "${spoke}-admin-kubeconfig" "${spoke}-kubeconfig"; do
    if "${oc_bin}" get secret "${secret}" -n "${spoke}" >/dev/null 2>&1; then
      if "${oc_bin}" get secret "${secret}" -n "${spoke}" \
        -o jsonpath='{.data.kubeconfig}' 2>/dev/null | base64 -d > "${out}" \
        && [[ -s "${out}" ]]; then
        log "  ${spoke}: using Hive/import secret ${secret}"
        return 0
      fi
    fi
  done
  return 1
}

create_secret_via_kubeconfig() {
  local spoke="$1" kc="$2"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "  ${spoke}: dry-run would oc --kubeconfig create secret ${SECRET_NAME} -n ${EDGE_NAMESPACE}"
    return 0
  fi
  KUBECONFIG="${kc}" "${oc_bin}" create namespace "${EDGE_NAMESPACE}" 2>/dev/null || true
  KUBECONFIG="${kc}" "${oc_bin}" create secret generic "${SECRET_NAME}" \
    --from-file=ca.crt="${tmpdir}/ca.crt" \
    --from-file=client.crt="${tmpdir}/client.crt" \
    --from-file=client.key="${tmpdir}/client.key" \
    -n "${EDGE_NAMESPACE}" \
    --dry-run=client -o yaml | KUBECONFIG="${kc}" "${oc_bin}" apply -f -
}

manifestwork_applied() {
  local oc_bin="$1" spoke="$2" status
  status="$("${oc_bin}" get manifestwork "${MANIFESTWORK_NAME}" -n "${spoke}" \
    -o jsonpath='{range .status.conditions[?(@.type=="Applied")]}{.status}{end}' \
    2>/dev/null || true)"
  [[ "${status}" == "True" ]]
}

wait_for_manifestwork_applied() {
  local spoke="$1" deadline
  deadline=$((SECONDS + MW_TIMEOUT_SECONDS))
  log "  ${spoke}: waiting for ManifestWork/${MANIFESTWORK_NAME} Applied=True (timeout=${MW_TIMEOUT_SECONDS}s)..."
  while true; do
    if manifestwork_applied "${oc_bin}" "${spoke}"; then
      log "  ${spoke}: ManifestWork/${MANIFESTWORK_NAME} Applied=True"
      return 0
    fi
    if [[ "${SECONDS}" -ge "${deadline}" ]]; then
      fail "timeout waiting for ManifestWork/${MANIFESTWORK_NAME} Applied on ${spoke}"
    fi
    sleep "${MW_INTERVAL_SECONDS}"
  done
}

create_secret_via_manifestwork() {
  local spoke="$1"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "  ${spoke}: dry-run would apply ManifestWork/${MANIFESTWORK_NAME} in ns ${spoke} and wait Applied"
    return 0
  fi
  if ! "${oc_bin}" get crd manifestworks.work.open-cluster-management.io >/dev/null 2>&1; then
    fail "ManifestWork CRD missing and no Hive kubeconfig for ${spoke}"
  fi
  "${oc_bin}" apply -f - <<EOF
apiVersion: work.open-cluster-management.io/v1
kind: ManifestWork
metadata:
  name: ${MANIFESTWORK_NAME}
  namespace: ${spoke}
  labels:
    app.kubernetes.io/part-of: adnr
    adnr.io/component: kafka-client-certs
spec:
  workload:
    manifests:
      - apiVersion: v1
        kind: Namespace
        metadata:
          name: ${EDGE_NAMESPACE}
          labels:
            app.kubernetes.io/part-of: adnr
      - apiVersion: v1
        kind: Secret
        metadata:
          name: ${SECRET_NAME}
          namespace: ${EDGE_NAMESPACE}
          labels:
            app.kubernetes.io/part-of: adnr
            adnr.io/component: kafka-client-certs
        type: Opaque
        data:
          ca.crt: ${ca_b64}
          client.crt: ${crt_b64}
          client.key: ${key_b64}
EOF
  log "  ${spoke}: ManifestWork/${MANIFESTWORK_NAME} applied"
  wait_for_manifestwork_applied "${spoke}"
}

log "Distributing ${SECRET_NAME} to ${#ADNR_SPOKE_NAMES[@]} spoke(s) (edge ns=${EDGE_NAMESPACE})..."
for spoke in "${ADNR_SPOKE_NAMES[@]}"; do
  kc="${tmpdir}/kubeconfig-${spoke}"
  if extract_hive_kubeconfig "${spoke}" "${kc}"; then
    create_secret_via_kubeconfig "${spoke}" "${kc}"
  else
    log "  ${spoke}: no Hive kubeconfig; using ManifestWork"
    create_secret_via_manifestwork "${spoke}"
  fi
done

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "OK: distribute-kafka-certs dry-run for ${#ADNR_SPOKE_NAMES[@]} spoke(s)"
else
  log "OK: distribute-kafka-certs completed for ${#ADNR_SPOKE_NAMES[@]} spoke(s)"
fi
exit 0
