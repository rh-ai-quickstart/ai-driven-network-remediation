# Mantis anomaly detection deployment

This directory contains the `ran-ml-service` detect InferenceService and Route.
It uses the predictor package and image in
[`../shared-ml-predictor/`](../shared-ml-predictor/) with `TASK=detect` and the
AD checkpoint from `hf://TomerG2/mantis-ad-telecomts`. The RCA deployment in
[`../ran-rca-ml-service/`](../ran-rca-ml-service/) uses that same image with a
different task and separate RCA artifacts.

From the repository root, use the shared deploy command:

```sh
TASK=detect SKIP_HF_UPLOAD=1 SKIP_BUILD=1 \
  ./model-serving/shared-ml-predictor/deploy/publish-and-deploy.sh
```

For source changes, build and push the shared image with
`make build-push-ran-ml-service`, then deploy both services with the new image
reference as needed. The detect deployment serves `POST /v1/detect`; configure
the hub with its full URL via `ADNR_DETECT_INFERENCE_URL`.
