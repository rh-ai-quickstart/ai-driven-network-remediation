# ran-ml-service

Mantis time-series ML predictor for TelecomTS anomaly detection (TASK=detect) and root cause analysis (TASK=classify).

## Deploy or refresh

Use the deploy script rather than applying the InferenceService by hand:

```sh
# Re-apply the existing InferenceService without rebuilding the image
# or re-uploading model weights.
SKIP_HF_UPLOAD=1 SKIP_BUILD=1 \
  ./model-serving/ran-ml-service/deploy/publish-and-deploy.sh
```

For predictor source changes, build and deploy a new, immutable image tag so
KServe creates a new revision:

```sh
SKIP_HF_UPLOAD=1 VERSION=<new-image-tag> \
  ./model-serving/ran-ml-service/deploy/publish-and-deploy.sh
```

### Building on macOS

On macOS, podman machine volume mounts may be unreliable. Use OpenShift BuildConfig to build on the cluster instead:

```sh
# Build on OpenShift cluster (recommended for macOS)
SKIP_HF_UPLOAD=1 USE_OPENSHIFT_BUILD=1 \
  ./model-serving/ran-ml-service/deploy/publish-and-deploy.sh
```

This uploads the build context to the cluster and uses the `ran-ml-overlay` BuildConfig to build the image.
The BuildConfig outputs to an ImageStreamTag that the InferenceService references.

The script waits for the InferenceService to become ready. KServe publishes the
external URL on the InferenceService status. Read it with:

```bash
oc get inferenceservice ran-ml-service -n model-serving -o jsonpath='{.status.url}/v1/detect'
```

Set that value as `ADNR_DETECT_INFERENCE_URL`. `ADNR_DETECT_TOKEN` is optional;
the detector sends `Authorization: Bearer` only when it is set. The manifest
namespace is `model-serving`. `ISVC_NAMESPACE` must stay `model-serving` unless
`deploy/inferenceservice.yaml` is edited to match.

## Endpoints

- `POST /v1/detect` — Binary anomaly detection on a 128x18 KPI window
- `GET /health` — Liveness probe
- `GET /ready` — Readiness probe (model loaded)

## Configuration

| Env Var | Description |
|---------|-------------|
| `TASK` | `detect` or `classify` |
| `MANTIS_MODEL_PATH` | Local path to `.pt` weights file |
| `MANTIS_CHECKPOINT` | HuggingFace backbone ID (default: `paris-noah/Mantis-8M`, baked into image) |
| `PORT` | HTTP port (default: 8080) |
