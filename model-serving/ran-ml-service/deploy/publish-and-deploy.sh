#!/usr/bin/env bash
# Publish weights to HuggingFace, build+push image, deploy InferenceService,
# and verify the endpoint. Run from the repo root.
#
# Usage:
#   ./model-serving/ran-ml-service/deploy/publish-and-deploy.sh
#
# Required env:
#   HF_TOKEN          — HuggingFace write token (or run `hf auth login` first)
#
# Optional env:
#   HF_REPO           — HuggingFace repo (default: rh-ai-quickstart/mantis-ad-telecomts)
#   WEIGHTS_PATH      — path to .pt weights file (default: model-serving/training/models/mantis_pretrained_ad.pt)
#   REGISTRY          — container registry (default: quay.io/rh-ai-quickstart)
#   VERSION           — image tag (default: from Makefile)
#   ISVC_NAMESPACE    — namespace for InferenceService (default: model-serving)
#   SKIP_BUILD        — set to 1 to skip image build/push
#   SKIP_HF_UPLOAD    — set to 1 to skip HuggingFace upload
#   SKIP_DEPLOY       — set to 1 to skip InferenceService deployment
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

HF_REPO="${HF_REPO:-rh-ai-quickstart/mantis-ad-telecomts}"
WEIGHTS_PATH="${WEIGHTS_PATH:-model-serving/training/models/mantis_pretrained_ad.pt}"
REGISTRY="${REGISTRY:-quay.io/rh-ai-quickstart}"
if [ -z "${VERSION:-}" ]; then
    VERSION="$(make -s version)"
fi
export REGISTRY VERSION
IMAGE="${REGISTRY}/noc-ran-ml-service:${VERSION}"
ISVC_NAMESPACE="${ISVC_NAMESPACE:-model-serving}"
SKIP_BUILD="${SKIP_BUILD:-}"
SKIP_HF_UPLOAD="${SKIP_HF_UPLOAD:-}"
SKIP_DEPLOY="${SKIP_DEPLOY:-}"
ISVC_YAML="model-serving/ran-ml-service/deploy/inferenceservice.yaml"

info()  { echo "==> $*"; }
error() { echo "ERROR: $*" >&2; exit 1; }

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
    info "Step 2: Building and pushing predictor image ($IMAGE)"
    make build-push-ran-ml-service REGISTRY="$REGISTRY" VERSION="$VERSION"
    info "Image pushed"
else
    info "Step 2: SKIPPED (SKIP_BUILD set)"
fi

# ── Step 3: Deploy InferenceService ────────────────────────────────
if [ -z "$SKIP_DEPLOY" ]; then
    info "Step 3: Deploying InferenceService to namespace $ISVC_NAMESPACE"

    oc whoami >/dev/null 2>&1 \
        || error "Not logged into OpenShift. Run: oc login <cluster-url>"

    oc create namespace "$ISVC_NAMESPACE" 2>/dev/null || true

    TMP_YAML=$(mktemp)
    trap 'rm -f "$TMP_YAML"' EXIT
    sed "s|image: .*noc-ran-ml-service:.*|image: ${IMAGE}|" "$ISVC_YAML" > "$TMP_YAML"
    info "Applying InferenceService with image $IMAGE"
    oc apply -f "$TMP_YAML"

    info "Waiting for InferenceService to become ready (timeout: 5m)..."
    oc wait --for=condition=Ready inferenceservice/ran-ml-service \
        -n "$ISVC_NAMESPACE" --timeout=300s
    info "InferenceService is ready"
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

DETECT_URL="${ISVC_URL}/v1/detect"
info "Endpoint: $DETECT_URL"

info "Testing health endpoint..."
curl -sf "${ISVC_URL}/health" | python3 -m json.tool

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
curl -sf -X POST "$DETECT_URL" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" | python3 -m json.tool

info ""
info "SUCCESS — endpoint is live."
info ""
info "Wire it into the hub chart:"
info "  helm upgrade hub ./hub/helm \\"
info "    --set-string ranAnomalyDetector.env.detectInferenceUrl=$DETECT_URL"
