#!/usr/bin/env bash
# Publish weights to HuggingFace, build+push image, deploy the InferenceService,
# and verify the endpoint. Run from the repo root.
#
# {.status.url} is the in-cluster Service (*.svc.cluster.local). Callers outside
# the cluster use the Route ran-ml-service-external. Do not name that Route
# ran-ml-service: KServe reconciles a Route with the InferenceService's name.
#
# Usage:
#   ./model-serving/ran-ml-service/deploy/publish-and-deploy.sh
#
# Required env:
#   HF_TOKEN          — HuggingFace write token (or run `hf auth login` first)
#
# Optional env:
#   HF_REPO              — HuggingFace repo (default: rh-ai-quickstart/mantis-ad-telecomts)
#   WEIGHTS_PATH         — path to .pt weights file (default: model-serving/training/models/mantis_pretrained_ad.pt)
#   REGISTRY             — container registry (default: quay.io/rh-ai-quickstart)
#   VERSION              — image tag (default: from Makefile)
#   ISVC_NAMESPACE       — namespace for the InferenceService and external Route
#                          (default: model-serving). Rewritten into both manifests
#                          before apply.
#   USE_OPENSHIFT_BUILD  — set to 1 to build on OpenShift cluster instead of local podman (recommended on macOS)
#   BUILDCONFIG_NAME     — OpenShift BuildConfig name (default: ran-ml-overlay)
#   SKIP_BUILD           — set to 1 to skip image build/push
#   SKIP_HF_UPLOAD       — set to 1 to skip HuggingFace upload
#   SKIP_DEPLOY          — set to 1 to skip InferenceService deployment
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

# TODO: replace with rh-ai-quickstart/mantis-ad-telecomts once the org HF repo is created
HF_REPO="${HF_REPO:-TomerG2/mantis-ad-telecomts}"
WEIGHTS_PATH="${WEIGHTS_PATH:-model-serving/training/models/mantis_pretrained_ad.pt}"
REGISTRY="${REGISTRY:-quay.io/rh-ai-quickstart}"
if [ -z "${VERSION:-}" ]; then
    VERSION="$(make -s version)"
fi
export REGISTRY VERSION
IMAGE="${REGISTRY}/noc-ran-ml-service:${VERSION}"
ISVC_NAMESPACE="${ISVC_NAMESPACE:-model-serving}"
USE_OPENSHIFT_BUILD="${USE_OPENSHIFT_BUILD:-}"
BUILDCONFIG_NAME="${BUILDCONFIG_NAME:-ran-ml-overlay}"
SKIP_BUILD="${SKIP_BUILD:-}"
SKIP_HF_UPLOAD="${SKIP_HF_UPLOAD:-}"
SKIP_DEPLOY="${SKIP_DEPLOY:-}"
ISVC_YAML="model-serving/ran-ml-service/deploy/inferenceservice.yaml"
ROUTE_YAML="model-serving/ran-ml-service/deploy/route.yaml"
EXTERNAL_ROUTE_NAME="ran-ml-service-external"

info()  { echo "==> $*"; }
error() { echo "ERROR: $*" >&2; exit 1; }

# inferenceservice.yaml and route.yaml both default metadata.namespace to
# model-serving. Rewrite that field so ISVC_NAMESPACE is the namespace applied.
render_manifest() {
    local src="$1" dest="$2"
    sed \
        -e "s|image: .*noc-ran-ml-service:.*|image: ${IMAGE}|" \
        -e "s|^  namespace: model-serving$|  namespace: ${ISVC_NAMESPACE}|" \
        "$src" > "$dest"
}

# ── Step 1: Upload weights to HuggingFace ──────────────────────────
if [ -z "$SKIP_HF_UPLOAD" ]; then
    info "Step 1: Uploading weights to HuggingFace ($HF_REPO)"

    [ -f "$WEIGHTS_PATH" ] || error "Weights file not found: $WEIGHTS_PATH"

    command -v hf >/dev/null 2>&1 \
        || error "hf CLI not found. Install: pip install huggingface-hub"

    if [ -n "${HF_TOKEN:-}" ]; then
        export HF_TOKEN
        info "Using HF_TOKEN from environment"
    else
        info "No HF_TOKEN set — assuming 'hf auth login' was already run"
    fi

    hf repos create "$HF_REPO" --type model --public --exist-ok 2>/dev/null \
        || info "Repo $HF_REPO already exists (or create failed — continuing)"

    hf upload "$HF_REPO" "$WEIGHTS_PATH" mantis_pretrained_ad.pt
    info "Weights uploaded to https://huggingface.co/$HF_REPO"
else
    info "Step 1: SKIPPED (SKIP_HF_UPLOAD set)"
fi

# ── Step 2: Build and push predictor image ─────────────────────────
if [ -z "$SKIP_BUILD" ]; then
    if [ -n "$USE_OPENSHIFT_BUILD" ]; then
        info "Step 2: Building image on OpenShift cluster ($BUILDCONFIG_NAME)"

        oc whoami >/dev/null 2>&1 \
            || error "Not logged into OpenShift. Run: oc login <cluster-url>"

        # Ensure Dockerfile symlink exists (BuildConfig expects Dockerfile, we use Containerfile)
        if [ ! -L "model-serving/ran-ml-service/Dockerfile" ]; then
            info "Creating Dockerfile symlink to Containerfile"
            ln -sf Containerfile model-serving/ran-ml-service/Dockerfile
        fi

        info "Starting build from model-serving/ran-ml-service directory"
        BUILD_NAME=$(oc start-build "$BUILDCONFIG_NAME" \
            --from-dir=model-serving/ran-ml-service \
            -n "$ISVC_NAMESPACE" \
            -o name)

        info "Build started: $BUILD_NAME"
        info "Following build logs..."
        oc logs -f "$BUILD_NAME" -n "$ISVC_NAMESPACE" || true

        info "Waiting for build to complete..."
        oc wait --for=condition=Complete "$BUILD_NAME" \
            -n "$ISVC_NAMESPACE" --timeout=600s

        info "OpenShift build complete"
    else
        info "Step 2: Building and pushing predictor image ($IMAGE)"
        make build-push-ran-ml-service REGISTRY="$REGISTRY" VERSION="$VERSION"
        info "Image pushed"
    fi
else
    info "Step 2: SKIPPED (SKIP_BUILD set)"
fi

# ── Step 3: Deploy InferenceService ────────────────────────────────
if [ -z "$SKIP_DEPLOY" ]; then
    info "Step 3: Deploying InferenceService to namespace $ISVC_NAMESPACE"

    oc whoami >/dev/null 2>&1 \
        || error "Not logged into OpenShift. Run: oc login <cluster-url>"

    oc create namespace "$ISVC_NAMESPACE" 2>/dev/null || true

    TMP_ISVC=$(mktemp)
    TMP_ROUTE=$(mktemp)
    trap 'rm -f "$TMP_ISVC" "$TMP_ROUTE"' EXIT
    render_manifest "$ISVC_YAML" "$TMP_ISVC"
    render_manifest "$ROUTE_YAML" "$TMP_ROUTE"
    info "Applying InferenceService with image $IMAGE in namespace $ISVC_NAMESPACE"
    oc apply -f "$TMP_ISVC"

    info "Waiting for InferenceService to become ready (timeout: 5m)..."
    oc wait --for=condition=Ready inferenceservice/ran-ml-service \
        -n "$ISVC_NAMESPACE" --timeout=300s
    info "InferenceService is ready"

    info "Applying external Route $EXTERNAL_ROUTE_NAME"
    oc apply -f "$TMP_ROUTE"
else
    info "Step 3: SKIPPED (SKIP_DEPLOY set)"
fi

# ── Step 4: Verify ─────────────────────────────────────────────────
info "Step 4: Verifying endpoint"

ISVC_URL=$(oc get inferenceservice ran-ml-service -n "$ISVC_NAMESPACE" \
    -o jsonpath='{.status.url}' 2>/dev/null || true)

if [ -z "$ISVC_URL" ]; then
    info "Could not retrieve InferenceService URL — verify manually"
    info "  oc get inferenceservice ran-ml-service -n $ISVC_NAMESPACE"
    exit 0
fi

info "In-cluster URL (reachable only from inside the cluster): ${ISVC_URL}/v1/detect"

info "Waiting for external Route host..."
ROUTE_HOST=""
for _ in $(seq 1 30); do
    ROUTE_HOST=$(oc get route "$EXTERNAL_ROUTE_NAME" -n "$ISVC_NAMESPACE" \
        -o jsonpath='{.spec.host}' 2>/dev/null || true)
    if [ -n "$ROUTE_HOST" ]; then
        break
    fi
    sleep 2
done
if [ -z "$ROUTE_HOST" ]; then
    error "Route $EXTERNAL_ROUTE_NAME has no host in namespace $ISVC_NAMESPACE"
fi

EXTERNAL_BASE="https://${ROUTE_HOST}"
DETECT_URL="${EXTERNAL_BASE}/v1/detect"
info "External URL: $DETECT_URL"

info "Testing health endpoint..."
curl -skf "${EXTERNAL_BASE}/health" | python3 -m json.tool

info "Testing /v1/detect with dummy payload..."
# Minimal 128-timestep payload (all zeros + TCP protocol encoding)
PAYLOAD=$(python3 -c "
import json
row = {
    'RSRP': 0, 'DL_BLER': 0, 'DL_MCS': 0, 'UL_BLER': 0, 'UL_MCS': 0,
    'UL_NPRB': 0, 'UL_SNR': 0, 'TX_Bytes': 0, 'RX_Bytes': 0,
    'Estimated_UL_Buffer': 0, 'PRBs_DL_Current': 0, 'PRBs_UL_Current': 0,
    'PRB_Utilization_DL': 0, 'PRB_Utilization_UL': 0,
    'UL_Protocol': 'TCP', 'UL_NumberOfPackets': 0,
    'DL_Protocol': 'TCP', 'DL_NumberOfPackets': 0,
}
print(json.dumps({'kpi_window': [row] * 128}))
")
curl -skf -X POST "$DETECT_URL" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" | python3 -m json.tool

info ""
info "SUCCESS — external endpoint is live."
info ""
info "From outside the cluster, including CI, set:"
info "  ADNR_DETECT_INFERENCE_URL=$DETECT_URL"
info "Same-cluster callers can use ${ISVC_URL}/v1/detect instead."
