# Mantis RCA serving

The `ran-rca-ml-service` KServe InferenceService uses the predictor package and
image in [`../shared-ml-predictor/`](../shared-ml-predictor/) with `TASK=classify`.
It runs separately from the detect InferenceService and the hub chart. Its
checkpoint and `rca_preprocessing.json` are provided by the pinned revision
`hf://TomerG2/mantis-rca-telecomts:1ffdc117e0f0c93ba779f0b5f69323d5f1c38163`.
The detect deployment obtains different AD weights from Hugging Face. The AD
checkpoint is incompatible.
KServe stages the RCA repository under `/mnt/models`, where `MANTIS_MODEL_PATH` selects the RCA
checkpoint. The image bakes the Mantis-8M backbone for offline startup.
The checkpoint and preprocessing file must match the SHA-256 values in the
InferenceService annotations and the validation report.

## Deploy the published model

The RCA artifacts were published to Hugging Face in issue #196. No local
checkpoint is needed to deploy them. From the repository root, with `oc`
logged in and a published shared predictor image available:

```sh
TASK=classify SKIP_HF_UPLOAD=1 SKIP_BUILD=1 IMAGE=<shared-image-reference> \
  ./model-serving/shared-ml-predictor/deploy/publish-and-deploy.sh
```

`STORAGE_URI` defaults to the same verified commit as parity CI in the
[InferenceService manifest](deploy/inferenceservice.yaml). KServe uses
`hf://owner/repo:revision` syntax for a pinned Hugging Face snapshot. The
shared command
applies that manifest and its Route, waits for readiness, checks task identity,
and smoke-tests `/v1/classify` from the in-cluster Service and TLS Route.
`SKIP_HF_UPLOAD=1` also lets a fresh checkout deploy without private model files
or Hugging Face write credentials. To force a new predictor revision after an
artifact refresh, set `RCA_ARTIFACT_COMMIT` to the newly verified 40-character
Hugging Face commit and a new `ARTIFACT_REVISION` rollout marker while keeping
the consumer URL:

```sh
TASK=classify SKIP_HF_UPLOAD=1 SKIP_BUILD=1 \
  IMAGE=<shared-image-reference> RCA_ARTIFACT_COMMIT=<new-verified-commit> \
  ARTIFACT_REVISION=<new-revision> \
  ./model-serving/shared-ml-predictor/deploy/publish-and-deploy.sh
```

Publishing a new checkpoint is a separate, explicit step: set `SKIP_DEPLOY=1`,
omit `SKIP_HF_UPLOAD`, provide `WEIGHTS_PATH` and `PREPROCESSING_PATH`, and
authenticate `hf` for write access. After upload, record the final repository
commit, verify both artifact hashes at that commit, update parity CI's
`RCA_ARTIFACT_COMMIT` and the manifest, then deploy using that commit. This
prevents an upload from deploying the previously pinned snapshot. Only the
publish path reads local files, verifies their hashes, and uploads them with
the model card. `RENDER_ONLY=1` prints the manifests
without publishing or deploying. `STORAGE_URI`, `HF_REPO`, manifest paths,
namespace, Service name, Route name, and smoke fixture are overridable.

For local inference with the available files, run `cd model-serving/shared-ml-predictor`
then `TASK=classify MANTIS_MODEL_PATH=../ran-rca-ml-service/.private-artifacts/issue-196/mantis_pretrained_rca.pt uv run --offline ran-ml-service`.
The backbone must already be in the Hugging Face cache for offline mode.

For a caller in the cluster, set `CLASSIFY_INFERENCE_URL` to
`http://ran-rca-ml-service-predictor.model-serving.svc.cluster.local/v1/classify`.
For an external caller, use
`https://$(oc get route ran-rca-ml-service-external -n model-serving -o jsonpath='{.spec.host}')/v1/classify`.
`CLASSIFY_TOKEN` only forwards a bearer header; the predictor has no built-in
token authentication. Set `ADNR_CLASSIFY_INFERENCE_URL` and optionally
`ADNR_CLASSIFY_TOKEN` for the hub deployment; a refresh of the predictor does
not require changing either value.

The validation report at `docs/model-runs/issue-196-rca-validation.json` records
dataset revision, split seed, training code revision, backbone revision, all ten
class names, checkpoint and preprocessing hashes, and measured held-out parity.
The RCA weights were newly trained in issue 196 because the historical
checkpoint was unavailable. The report is the available provenance; no original
MLflow run is claimed for these weights.

## RCA checkpoint recovery

`scripts/train_rca.py` runs only the 10-class pretrained Mantis RCA task from
the TelecomTS notebook. It fixes the dataset revision, backbone revision,
80/20 seed-42 split, class order, and defaults of 35 epochs, batch size 64,
and learning rate `1e-4`. A non-35-epoch `--epochs` run is marked exploratory even
if its metrics pass. It requires a private, durable mounted output directory. No model is
published or deployed by this command.

The script reads the three Arrow shards of the pinned TelecomTS revision. If
they are not already cached, fetch them once with `datasets` using the revision
`1b3a88a440f4922edfcd1e230e42c003c0e13bdf` and
`data_files={"full": "**/chunked.jsonl"}`, then pass the resulting Arrow
directory as `--dataset-dir`.

```sh
cd model-serving/shared-ml-predictor
uv sync --locked --group train
uv run --locked --group train python ../ran-rca-ml-service/scripts/train_rca.py \
  --output-dir /private/durable-storage/rca-run-196
```

The output directory contains `mantis_pretrained_rca.pt`, the paired
`rca_preprocessing.json`, and `rca_validation_report.json`. The report records
both serving-style and notebook-style scores, the 10-class confusion matrix
and per-class metrics, separate Jamming predictions, artifact SHA-256 hashes,
dependency versions, and provenance. Only the serving-style score sets the
pass/fail status. Retain a failed report for diagnosis; a failed gate is not a
validated checkpoint.
