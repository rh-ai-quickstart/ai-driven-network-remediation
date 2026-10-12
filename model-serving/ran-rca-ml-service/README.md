# Mantis RCA serving

The `ran-rca-ml-service` KServe InferenceService uses the shared predictor image
with `TASK=classify`. It runs separately from the hub chart. Publish the
original `mantis_pretrained_rca.pt` export and `rca_preprocessing.json` in the
same Hugging Face repository before applying these manifests. The checkpoint
must be the pretrained RCA export from the TelecomTS notebook; the AD checkpoint
is incompatible. Set `STORAGE_URI` to the published repository. KServe stages
its contents under `/mnt/models`, where `MANTIS_MODEL_PATH` selects the RCA
checkpoint. Use a new predictor image tag containing the classifier code.

```sh
oc apply -f model-serving/ran-rca-ml-service/deploy/inferenceservice.yaml
oc apply -f model-serving/ran-rca-ml-service/deploy/route.yaml
oc get inferenceservice ran-rca-ml-service -n model-serving
```

For a caller in the cluster, set `CLASSIFY_INFERENCE_URL` to
`http://ran-rca-ml-service-predictor.model-serving.svc.cluster.local/v1/classify`.
For an external caller, use
`https://$(oc get route ran-rca-ml-service-external -n model-serving -o jsonpath='{.spec.host}')/v1/classify`.
`CLASSIFY_TOKEN` only forwards a bearer header; the predictor has no built-in
token authentication.
