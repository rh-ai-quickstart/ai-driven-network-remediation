#!/usr/bin/env bash
# Manually publish and deploy either task of the shared predictor image.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"
TASK="${TASK:-detect}"
case "$TASK" in
  detect)
    HF_REPO="${HF_REPO:-TomerG2/mantis-ad-telecomts}"
    WEIGHTS_PATH="${WEIGHTS_PATH:-model-serving/training/models/mantis_pretrained_ad.pt}"
    MODEL_FILENAME=mantis_pretrained_ad.pt
    ISVC_NAME="${ISVC_NAME:-ran-ml-service}"
    MANIFEST_DIR="${MANIFEST_DIR:-model-serving/ran-ml-service/deploy}"
    SMOKE_FIXTURE="${SMOKE_FIXTURE:-}"
    ;;
  classify)
    HF_REPO="${HF_REPO:-TomerG2/mantis-rca-telecomts}"
    RCA_ARTIFACT_COMMIT="${RCA_ARTIFACT_COMMIT:-1ffdc117e0f0c93ba779f0b5f69323d5f1c38163}"
    [[ "$RCA_ARTIFACT_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo 'RCA_ARTIFACT_COMMIT must be a 40-character commit SHA' >&2; exit 1; }
    DEFAULT_STORAGE_URI="hf://${HF_REPO}:${RCA_ARTIFACT_COMMIT}"
    WEIGHTS_PATH="${WEIGHTS_PATH:-model-serving/ran-rca-ml-service/.private-artifacts/issue-196/mantis_pretrained_rca.pt}"
    PREPROCESSING_PATH="${PREPROCESSING_PATH:-model-serving/ran-rca-ml-service/.private-artifacts/issue-196/rca_preprocessing.json}"
    MODEL_FILENAME=mantis_pretrained_rca.pt
    CHECKPOINT_SHA256="${CHECKPOINT_SHA256:-0ecd02d50eed50130476c0c543503f9f3d1595507502664d3e5b8fb01c3eccfe}"
    PREPROCESSING_SHA256="${PREPROCESSING_SHA256:-1a8e0144bb4a65f48133c4ad36491aba6432b817d64b8d3521a3c8de913ddeae}"
    ISVC_NAME="${ISVC_NAME:-ran-rca-ml-service}"
    MANIFEST_DIR="${MANIFEST_DIR:-model-serving/ran-rca-ml-service/deploy}"
    SMOKE_FIXTURE="${SMOKE_FIXTURE:-hub/telco-oran/src/telco_oran/fixtures/antenna_failure.json}"
    SMOKE_EXPECTED_CLASS="${SMOKE_EXPECTED_CLASS:-Antenna Failure}"
    SMOKE_EXPECTED_INDEX="${SMOKE_EXPECTED_INDEX:-0}"
    ;;
  *) echo "TASK must be detect or classify" >&2; exit 1 ;;
esac
CHECKPOINT_SHA256="${CHECKPOINT_SHA256:-}"
PREPROCESSING_SHA256="${PREPROCESSING_SHA256:-}"

REGISTRY="${REGISTRY:-quay.io/rh-ai-quickstart}"
VERSION="${VERSION:-$(make -s version)}"
IMAGE="${IMAGE:-${REGISTRY}/noc-ran-ml-service:${VERSION}}"
ISVC_NAMESPACE="${ISVC_NAMESPACE:-model-serving}"
ISVC_YAML="${ISVC_YAML:-${MANIFEST_DIR}/inferenceservice.yaml}"
ROUTE_YAML="${ROUTE_YAML:-${MANIFEST_DIR}/route.yaml}"
EXTERNAL_ROUTE_NAME="${EXTERNAL_ROUTE_NAME:-${ISVC_NAME}-external}"
STORAGE_URI="${STORAGE_URI:-${DEFAULT_STORAGE_URI:-hf://${HF_REPO}}}"
ARTIFACT_REVISION="${ARTIFACT_REVISION:-}"
BUILDCONFIG_NAME="${BUILDCONFIG_NAME:-ran-ml-overlay}"
export REGISTRY VERSION TASK IMAGE ISVC_NAMESPACE ISVC_NAME EXTERNAL_ROUTE_NAME \
  STORAGE_URI ARTIFACT_REVISION MODEL_FILENAME SMOKE_EXPECTED_CLASS SMOKE_EXPECTED_INDEX \
  CHECKPOINT_SHA256 PREPROCESSING_SHA256

render_manifest() {
  SOURCE="$1" DESTINATION="$2" KIND="$3" python3 - <<'PY'
import os
import re
from pathlib import Path

source = Path(os.environ["SOURCE"]).read_text()

def replace(pattern, replacement):
    global source
    source, count = re.subn(pattern, lambda _: replacement, source, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"expected one {pattern!r} in {os.environ['SOURCE']}")

replace(r"^  namespace: .*", f"  namespace: {os.environ['ISVC_NAMESPACE']}")
if os.environ["KIND"] == "isvc":
    replace(r"^  name: ran-(?:rca-)?ml-service$", f"  name: {os.environ['ISVC_NAME']}")
    replace(r"^        image: .*", f"        image: {os.environ['IMAGE']}")
    replace(r"^            value: (detect|classify)$", f"            value: {os.environ['TASK']}")
    replace(r"^            value: /mnt/models/.*\.pt$", f"            value: /mnt/models/{os.environ['MODEL_FILENAME']}")
    storage_value = f"            value: {os.environ['STORAGE_URI']}"
    if os.environ["ARTIFACT_REVISION"]:
        revision = os.environ["ARTIFACT_REVISION"].replace("'", "''")
        storage_value += f"\n          - name: ARTIFACT_REVISION\n            value: '{revision}'"
    replace(r"^            value: hf://.*", storage_value)
    if os.environ["TASK"] == "classify":
        replace(r"^    ai.redhat.com/rca-checkpoint-sha256: .*", f"    ai.redhat.com/rca-checkpoint-sha256: {os.environ['CHECKPOINT_SHA256']}")
        replace(r"^    ai.redhat.com/rca-preprocessing-sha256: .*", f"    ai.redhat.com/rca-preprocessing-sha256: {os.environ['PREPROCESSING_SHA256']}")
    if os.environ["ARTIFACT_REVISION"]:
        replace(r"^  annotations:$", "  annotations:\n    ai.redhat.com/artifact-revision: '" + revision + "'")
else:
    replace(r"^  name: ran-(?:rca-)?ml-service-external$", f"  name: {os.environ['EXTERNAL_ROUTE_NAME']}")
    replace(r"^    name: ran-(?:rca-)?ml-service-predictor$", f"    name: {os.environ['ISVC_NAME']}-predictor")
Path(os.environ["DESTINATION"]).write_text(source)
PY
}

verify_sha256() {
  local path="$1" expected="$2" actual
  actual=$(shasum -a 256 "$path" | cut -d ' ' -f 1)
  [ "$actual" = "$expected" ] || { echo "SHA-256 mismatch: $path ($actual)" >&2; exit 1; }
}

render_manifests() {
  tmp_isvc=$(mktemp)
  tmp_route=$(mktemp)
  trap 'rm -f "$tmp_isvc" "$tmp_route"' EXIT
  render_manifest "$ISVC_YAML" "$tmp_isvc" isvc
  render_manifest "$ROUTE_YAML" "$tmp_route" route
}

if [ -n "${RENDER_ONLY:-}" ]; then
  render_manifests
  cat "$tmp_isvc" "$tmp_route"
  exit 0
fi

if [ "$TASK" = classify ] && [ -z "${SKIP_HF_UPLOAD:-}" ] && [ -z "${SKIP_DEPLOY:-}" ]; then
  echo 'Publish RCA artifacts with SKIP_DEPLOY=1, then deploy their verified commit with RCA_ARTIFACT_COMMIT' >&2
  exit 1
fi

if [ -z "${SKIP_HF_UPLOAD:-}" ]; then
  [ -f "$WEIGHTS_PATH" ] || { echo "Missing weights: $WEIGHTS_PATH" >&2; exit 1; }
  if [ "$TASK" = classify ]; then
    [ -f "$PREPROCESSING_PATH" ] || { echo "Missing preprocessing: $PREPROCESSING_PATH" >&2; exit 1; }
    verify_sha256 "$WEIGHTS_PATH" "$CHECKPOINT_SHA256"
    verify_sha256 "$PREPROCESSING_PATH" "$PREPROCESSING_SHA256"
  fi
  command -v hf >/dev/null || { echo 'hf CLI not found' >&2; exit 1; }
  hf repos create "$HF_REPO" --type model --public --exist-ok
  hf upload "$HF_REPO" "$WEIGHTS_PATH" "$MODEL_FILENAME"
  if [ "$TASK" = classify ]; then
    hf upload "$HF_REPO" "$PREPROCESSING_PATH" rca_preprocessing.json
    hf upload "$HF_REPO" model-serving/ran-rca-ml-service/rca-model-card.md README.md
    if [ -n "$ARTIFACT_REVISION" ]; then
      release_file=$(mktemp)
      trap 'rm -f "$release_file"' EXIT
      RELEASE_FILE="$release_file" CHECKPOINT_SHA256="$CHECKPOINT_SHA256" \
        PREPROCESSING_SHA256="$PREPROCESSING_SHA256" python3 - <<'PY'
import json
import os
from pathlib import Path

Path(os.environ["RELEASE_FILE"]).write_text(json.dumps({
    "artifact_revision": os.environ["ARTIFACT_REVISION"],
    "checkpoint_sha256": os.environ["CHECKPOINT_SHA256"],
    "preprocessing_sha256": os.environ["PREPROCESSING_SHA256"],
}, indent=2) + "\n")
PY
      hf upload "$HF_REPO" "$release_file" release.json
      rm -f "$release_file"
      trap - EXIT
    fi
  fi
fi

if [ -z "${SKIP_BUILD:-}" ]; then
  if [ -n "${USE_OPENSHIFT_BUILD:-}" ]; then
    oc whoami >/dev/null
    build_context=$(mktemp -d)
    trap 'rm -rf "$build_context"' EXIT
    cp model-serving/shared-ml-predictor/{pyproject.toml,uv.lock,README.md} "$build_context/"
    cp model-serving/shared-ml-predictor/Containerfile "$build_context/Dockerfile"
    cp -R model-serving/shared-ml-predictor/src "$build_context/src"
    build_name=$(oc start-build "${BUILDCONFIG_NAME}" --from-dir="$build_context" -n "$ISVC_NAMESPACE" -o name)
    rm -rf "$build_context"
    trap - EXIT
    oc logs -f "$build_name" -n "$ISVC_NAMESPACE"
    oc wait --for=condition=Complete "$build_name" -n "$ISVC_NAMESPACE" --timeout=600s
    image_stream_tag=$(oc get buildconfig "$BUILDCONFIG_NAME" -n "$ISVC_NAMESPACE" -o jsonpath='{.spec.output.to.name}')
    [ -n "$image_stream_tag" ] || { echo 'BuildConfig has no ImageStreamTag output' >&2; exit 1; }
    IMAGE="image-registry.openshift-image-registry.svc:5000/${ISVC_NAMESPACE}/${image_stream_tag}"
    export IMAGE
  else
    make build-push-ran-ml-service REGISTRY="$REGISTRY" VERSION="$VERSION"
  fi
fi

if [ -n "${SKIP_DEPLOY:-}" ]; then
  echo 'Deployment skipped'
  exit 0
fi

render_manifests
oc whoami >/dev/null
oc create namespace "$ISVC_NAMESPACE" --dry-run=client -o yaml | oc apply -f -
oc apply -f "$tmp_isvc"
oc wait --for=condition=Ready "inferenceservice/$ISVC_NAME" -n "$ISVC_NAMESPACE" --timeout=300s
oc apply -f "$tmp_route"

internal_base=$(oc get inferenceservice "$ISVC_NAME" -n "$ISVC_NAMESPACE" -o jsonpath='{.status.url}')
[ -n "$internal_base" ] || { echo 'InferenceService has no status.url' >&2; exit 1; }
oc exec -i -n "$ISVC_NAMESPACE" "deployment/${ISVC_NAME}-predictor" -- python - "$internal_base" "$TASK" <<'PY'
import json
import sys
from urllib.request import urlopen

base, task = sys.argv[1:]
for path, expected in (("/health", {"status": "ok", "task": task}),
                       ("/ready", {"ready": True, "task": task})):
    with urlopen(base + path, timeout=15) as response:
        assert json.load(response) == expected
PY
external_host=$(oc get route "$EXTERNAL_ROUTE_NAME" -n "$ISVC_NAMESPACE" -o jsonpath='{.spec.host}')
[ -n "$external_host" ] || { echo 'Route has no host' >&2; exit 1; }
external_base="https://${external_host}"
endpoint="${external_base}/v1/${TASK}"
health=$(curl --fail-with-body --silent --show-error "${external_base}/health")
ready=$(curl --fail-with-body --silent --show-error "${external_base}/ready")
HEALTH="$health" READY="$ready" python3 - <<'PY'
import json
import os
assert json.loads(os.environ["HEALTH"]) == {"status": "ok", "task": os.environ["TASK"]}
assert json.loads(os.environ["READY"]) == {"ready": True, "task": os.environ["TASK"]}
PY

if [ -n "$SMOKE_FIXTURE" ]; then
  payload=$(python3 - "$SMOKE_FIXTURE" <<'PY'
import json
import sys
with open(sys.argv[1]) as fixture:
    print(json.dumps({"kpi_window": json.load(fixture)["kpi_window"]}))
PY
  )
else
  payload=$(python3 - <<'PY'
import json
row = {key: 0 for key in (
    "RSRP", "DL_BLER", "DL_MCS", "UL_BLER", "UL_MCS", "UL_NPRB", "UL_SNR",
    "TX_Bytes", "RX_Bytes", "Estimated_UL_Buffer", "PRBs_DL_Current",
    "PRBs_UL_Current", "PRB_Utilization_DL", "PRB_Utilization_UL",
    "UL_NumberOfPackets", "DL_NumberOfPackets")}
row.update(UL_Protocol="TCP", DL_Protocol="TCP")
print(json.dumps({"kpi_window": [row] * 128}))
PY
  )
fi

internal_response=$(oc exec -i -n "$ISVC_NAMESPACE" "deployment/${ISVC_NAME}-predictor" -- \
  python -c '
import sys
from urllib.request import Request, urlopen

base, task = sys.argv[1:]
request = Request(
    base + "/v1/" + task,
    data=sys.stdin.buffer.read(),
    headers={"Content-Type": "application/json"},
)
print(urlopen(request, timeout=30).read().decode())
' \
  "$internal_base" "$TASK" <<< "$payload")
external_response=$(curl --fail-with-body --silent --show-error -X POST "$endpoint" -H 'Content-Type: application/json' -d "$payload")
for response in "$internal_response" "$external_response"; do
RESPONSE="$response" python3 - <<'PY'
import json
import os
body = json.loads(os.environ["RESPONSE"])
assert body.get("class_index") in range(10 if os.environ["TASK"] == "classify" else 2), body
assert 0 <= body.get("confidence", -1) <= 1, body
assert isinstance(body.get("class" if os.environ["TASK"] == "classify" else "label"), str), body
if os.environ["TASK"] == "classify":
    assert body["class"] == os.environ["SMOKE_EXPECTED_CLASS"], body
    assert body["class_index"] == int(os.environ["SMOKE_EXPECTED_INDEX"]), body
print(json.dumps(body, indent=2))
PY
done
echo "Ready: $endpoint"
echo "Same-cluster: ${internal_base}/v1/${TASK}"
