# Shared RAN ML predictor

This is the single source package and image build for two independently deployed
InferenceServices. `ran-ml-service` runs it with `TASK=detect` and its AD
checkpoint; `ran-rca-ml-service` runs the same image with `TASK=classify`, a
different RCA checkpoint, and `rca_preprocessing.json`. Neither deployment
loads the other task's weights. Their manifests and task-specific instructions
live in [`../ran-ml-service/`](../ran-ml-service/) and
[`../ran-rca-ml-service/`](../ran-rca-ml-service/).

The shared [deploy command](deploy/publish-and-deploy.sh) selects the manifest,
artifact repository, and checkpoint by `TASK`. The checkpoints are published
separately to Hugging Face and staged into each pod under `/mnt/models`; the
container image includes the common predictor code and offline Mantis backbone.

## Deploy or refresh detect

Use the deploy script rather than applying the InferenceService by hand:

```sh
# Re-apply the existing InferenceService without rebuilding the image
# or re-uploading model weights.
SKIP_HF_UPLOAD=1 SKIP_BUILD=1 \
  ./model-serving/shared-ml-predictor/deploy/publish-and-deploy.sh
```

For predictor source changes, build and deploy a new, immutable image tag so
KServe creates a new revision:

```sh
SKIP_HF_UPLOAD=1 VERSION=<new-image-tag> \
  ./model-serving/shared-ml-predictor/deploy/publish-and-deploy.sh
```

### Building on macOS

On macOS, podman machine volume mounts may be unreliable. Use OpenShift BuildConfig to build on the cluster instead:

```sh
# Build on OpenShift cluster (recommended for macOS)
SKIP_HF_UPLOAD=1 USE_OPENSHIFT_BUILD=1 \
  ./model-serving/shared-ml-predictor/deploy/publish-and-deploy.sh
```

This uploads the build context to the cluster and uses the `ran-ml-overlay` BuildConfig to build the image.
The BuildConfig outputs to an ImageStreamTag that the InferenceService references.

The script rewrites `metadata.namespace` from `ISVC_NAMESPACE` (default `model-serving`) and waits for the InferenceService to become ready. KServe's `{.status.url}` is the in-cluster Service (`*.svc.cluster.local`). Callers outside the cluster use the Route `ran-ml-service-external`:

```bash
oc get route ran-ml-service-external -n "$ISVC_NAMESPACE" -o jsonpath='https://{.spec.host}/v1/detect'
```

Set that value as `ADNR_DETECT_INFERENCE_URL`. `ADNR_DETECT_TOKEN` is optional; the detector sends `Authorization: Bearer` only when it is set. A same-cluster caller can use `{.status.url}/v1/detect` instead.

## Endpoints

- `POST /v1/detect` — Binary anomaly detection on a 128x18 KPI window
- `POST /v1/classify` — Ten-class root cause classification on the same window
- `GET /health` — Liveness probe
- `GET /ready` — Readiness probe (model loaded)

## Configuration

| Env Var | Description |
|---------|-------------|
| `TASK` | `detect` or `classify` |
| `MANTIS_MODEL_PATH` | Local path to `.pt` weights file |
| `MANTIS_CHECKPOINT` | HuggingFace backbone ID (default: `paris-noah/Mantis-8M`, baked into image) |
| `PORT` | HTTP port (default: 8080) |

For local RCA inference, place `mantis_pretrained_rca.pt` and
`rca_preprocessing.json` in one directory, then run:

```sh
cd model-serving/shared-ml-predictor
TASK=classify MANTIS_MODEL_PATH=/path/to/mantis_pretrained_rca.pt uv run ran-ml-service
```

The Mantis-8M backbone must be present in the Hugging Face cache for offline
mode. `POST /v1/classify` accepts `{"kpi_window": [...]}` with all 128 rows and
returns `class`, `class_index`, and `confidence`. `/ready` stays unavailable if
the checkpoint task, head dimensions, or preprocessing artifact (including its
ordered RCA class mapping) is invalid.

Before running the RCA checkpoint parity test locally, run `make generate-fixtures`
from the repository root to create the shared TelecomTS samples.
