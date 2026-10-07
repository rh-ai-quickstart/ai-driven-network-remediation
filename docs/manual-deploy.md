# Deploy to OpenShift

0. Set environment variables

$NAMESPACE default `hub`: namespace used to install the Hub
$REGISTRY default `quay.io/rh-ai-quickstart`: Remote container registry
$VERSION default `0.1.0`: Versions for all container images

`make helm-install` and `make integration-tests` require the ADNR-backed Llama Stack model to be configured. Set:

- `$ADNR_LLM_ID`: model identifier registered in Llama Stack
- `$ADNR_LLM_URL`: remote OpenAI-compatible or vLLM endpoint
- `$ADNR_LLM_TOKEN`: bearer token for that endpoint

You can start from `.env.example` to populate these values for local development.

RHPDS can be used to validate this flow. See
[`APPENG-5217`](https://redhat.atlassian.net/browse/APPENG-5217) for the RHPDS
environment and configure a model in MaaS there, or use any other compatible
Model as a Service endpoint you prefer.

Example:

```bash
export ADNR_LLM_ID='granite-3-2-8b-instruct'
export ADNR_LLM_URL='https://litellm-prod.apps.maas.redhatworkshops.io/v1'
export ADNR_LLM_TOKEN='your-api-token-from-RHPDS-MaaS'
```

1. Login to OpenShift remote cluster. For instance:

```bash
oc login --token=$TOKEN --server=https://$LAB.openshift.com:6443
```

2. Deploy the hub:
The deploy will use images from the $REGISTRY

```bash
make helm-install
```

For local development and demos, `make helm-install` enables the AAP mock by default.
For environments that should use a real Ansible Automation Platform controller instead,
disable the mock during deployment:

```bash
ENABLE_AAP_MOCK=false make helm-install
```

3. Run Integration Tests:

```bash
make integration-tests
```

4. Undeploy the hub:

```bash
make helm-uninstall
```

# Build (custom image from the codebase)

In the following example we assume that we want to use `quay.io/fercoli` as repo,
and `0.0.1.Verify` as custom version for the images:

1. Login to quay.io

```bash
podman login quay.io
```

2. Build and tag the images

```bash
REGISTRY=quay.io/fercoli VERSION=0.0.1.Verify make build-all-images
```

3. Build and tag the images

```bash
REGISTRY=quay.io/fercoli VERSION=0.0.1.Verify make push-all-images
```

4. Deploy the hub:
The deploy will use images from the $REGISTRY

```bash
REGISTRY=quay.io/fercoli VERSION=0.0.1.Verify make helm-install
```

If this deployment should target a real AAP controller rather than the built-in mock,
disable the mock explicitly:

```bash
REGISTRY=quay.io/fercoli VERSION=0.0.1.Verify ENABLE_AAP_MOCK=false make helm-install
```

5. Run Integration Tests:

```bash
make integration-tests
```

6. Verify you're using the right deployment:

```bash
oc get deploy hub-chatbot-service -o jsonpath='{.spec.template.spec.containers[*].image}'
```

The output should like:

> quay.io/fercoli/noc-chatbot-service:0.0.1.Verify(base)