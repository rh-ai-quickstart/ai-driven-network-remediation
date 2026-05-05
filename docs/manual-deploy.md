# Deploy to OpenShift

0. [Optionally] Set environment variables

$NAMESPACE default `hub`: namespace used to install the Hub
$REGISTRY default `quay.io/rh-ai-quickstart`: Remote container registry
$VERSION default `0.1.0`: Versions for all container images

1. Login to OpenShift remote cluster. For instance:

```bash
oc login --token=$TOKEN --server=https://$LAB.openshift.com:6443
```

2. Deploy the hub:
The deploy will use images from the $REGISTRY

```bash
make helm-install
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