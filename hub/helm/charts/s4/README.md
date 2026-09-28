# S4 Helm Chart

S4 (Super Simple Storage Service) is a lightweight, self-contained S3-compatible storage solution.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.x
- PV provisioner support in the underlying infrastructure (for persistent storage)

## Installation

### Quick Start

```bash
# Clone the repository
git clone https://github.com/rh-aiservices-bu/s4.git
cd s4

# Install with authentication (required by default)
helm install s4 ./charts/s4 --namespace s4 --create-namespace \
  --set auth.username=admin \
  --set auth.password=your-secure-password

# Install with custom values
helm install s4 ./charts/s4 --namespace s4 --create-namespace -f my-values.yaml
```

## Uninstallation

```bash
helm uninstall s4 --namespace s4
```

**Note:** PersistentVolumeClaims are not deleted automatically. To remove all data:

```bash
kubectl delete pvc -l app.kubernetes.io/instance=s4 -n s4
```

## Configuration

The following table lists the configurable parameters and their default values.

### Image Configuration

| Parameter          | Description                | Default                       |
| ------------------ | -------------------------- | ----------------------------- |
| `image.repository` | Container image repository | `quay.io/rh-aiservices-bu/s4` |
| `image.tag`        | Container image tag        | `latest`                      |
| `image.pullPolicy` | Image pull policy          | `Always`                      |
| `imagePullSecrets` | Image pull secrets         | `[]`                          |

### S3 Configuration

| Parameter            | Description                            | Default                 |
| -------------------- | -------------------------------------- | ----------------------- |
| `s3.endpoint`        | S3 endpoint URL                        | `http://localhost:7480` |
| `s3.region`          | S3 region                              | `us-east-1`             |
| `s3.accessKeyId`     | S3 access key ID                       | `s4admin`               |
| `s3.secretAccessKey` | S3 secret access key                   | `s4secret`              |
| `s3.existingSecret`  | Use existing secret for S3 credentials | `""`                    |

### Authentication Configuration

| Parameter                 | Description                              | Default |
| ------------------------- | ---------------------------------------- | ------- |
| `auth.enabled`            | Enable UI authentication                 | `true`  |
| `auth.username`           | Username for UI login                    | `""`    |
| `auth.password`           | Password for UI login                    | `""`    |
| `auth.jwtSecret`          | JWT secret key (auto-generated if empty) | `""`    |
| `auth.jwtExpirationHours` | JWT token expiration in hours            | `8`     |
| `auth.cookieRequireHttps` | Require HTTPS for cookies                | `true`  |

### Storage Configuration

| Parameter                            | Description                        | Default         |
| ------------------------------------ | ---------------------------------- | --------------- |
| `storage.localPaths`                 | Local storage paths                | `""` (disabled) |
| `storage.maxFileSizeGB`              | Maximum file size in GB            | `20`            |
| `storage.maxConcurrentTransfers`     | Maximum concurrent transfers       | `2`             |
| `storage.data.size`                  | RGW data volume size               | `10Gi`          |
| `storage.data.storageClass`          | Storage class for data volume      | `""`            |
| `storage.data.existingClaim`         | Use existing PVC for data          | `""`            |
| `storage.localStorage.enabled`       | Enable local storage volume        | `false`         |
| `storage.localStorage.size`          | Local storage volume size          | `50Gi`          |
| `storage.localStorage.storageClass`  | Storage class for local storage    | `""`            |
| `storage.localStorage.existingClaim` | Use existing PVC for local storage | `""`            |

### Resource Configuration

| Parameter                   | Description    | Default |
| --------------------------- | -------------- | ------- |
| `resources.requests.cpu`    | CPU request    | `250m`  |
| `resources.requests.memory` | Memory request | `512Mi` |
| `resources.limits.cpu`      | CPU limit      | `2000m` |
| `resources.limits.memory`   | Memory limit   | `2Gi`   |

### Service Configuration

| Parameter                  | Description                        | Default     |
| -------------------------- | ---------------------------------- | ----------- |
| `service.type`             | Service type                       | `ClusterIP` |
| `service.port`             | Web UI port                        | `5000`      |
| `service.s3Port`           | S3 API port                        | `7480`      |
| `service.nodePort.enabled` | Enable additional NodePort service | `false`     |
| `service.nodePort.webPort` | NodePort for web UI                | `""`        |
| `service.nodePort.s3Port`  | NodePort for S3 API                | `""`        |

### Ingress Configuration (Web UI)

| Parameter             | Description                           | Default |
| --------------------- | ------------------------------------- | ------- |
| `ingress.enabled`     | Enable ingress for Web UI (port 5000) | `false` |
| `ingress.className`   | Ingress class name                    | `""`    |
| `ingress.annotations` | Ingress annotations                   | `{}`    |
| `ingress.hosts`       | Ingress hosts configuration           | `[]`    |
| `ingress.tls`         | Ingress TLS configuration             | `[]`    |

### Ingress Configuration (S3 API)

Optionally expose the S3 API (port 7480) externally via a separate Ingress.

> **Warning:** Enabling this exposes the S3 API outside the cluster. Ensure proper authentication and network policies are in place.

| Parameter                   | Description                           | Default |
| --------------------------- | ------------------------------------- | ------- |
| `ingress.s3Api.enabled`     | Enable ingress for S3 API (port 7480) | `false` |
| `ingress.s3Api.className`   | Ingress class name                    | `""`    |
| `ingress.s3Api.annotations` | Ingress annotations                   | `{}`    |
| `ingress.s3Api.hosts`       | Ingress hosts configuration           | `[]`    |
| `ingress.s3Api.tls`         | Ingress TLS configuration             | `[]`    |

### OpenShift Route Configuration (Web UI)

| Parameter                                 | Description                                   | Default    |
| ----------------------------------------- | --------------------------------------------- | ---------- |
| `route.enabled`                           | Enable OpenShift Route for Web UI (port 5000) | `true`     |
| `route.host`                              | Route hostname                                | `""`       |
| `route.path`                              | Route path                                    | `""`       |
| `route.annotations`                       | Route annotations                             | `{}`       |
| `route.tls.termination`                   | TLS termination type                          | `edge`     |
| `route.tls.insecureEdgeTerminationPolicy` | Insecure edge policy                          | `Redirect` |

### OpenShift Route Configuration (S3 API)

Optionally expose the S3 API (port 7480) externally via a separate Route.

> **Warning:** Enabling this exposes the S3 API outside the cluster. Ensure proper authentication and network policies are in place.

| Parameter                                       | Description                                   | Default    |
| ----------------------------------------------- | --------------------------------------------- | ---------- |
| `route.s3Api.enabled`                           | Enable OpenShift Route for S3 API (port 7480) | `false`    |
| `route.s3Api.host`                              | Route hostname                                | `""`       |
| `route.s3Api.path`                              | Route path                                    | `""`       |
| `route.s3Api.annotations`                       | Route annotations                             | `{}`       |
| `route.s3Api.tls.termination`                   | TLS termination type                          | `edge`     |
| `route.s3Api.tls.insecureEdgeTerminationPolicy` | Insecure edge policy                          | `Redirect` |

### Security Configuration

| Parameter                                  | Description                | Default |
| ------------------------------------------ | -------------------------- | ------- |
| `serviceAccount.create`                    | Create service account     | `true`  |
| `serviceAccount.name`                      | Service account name       | `""`    |
| `podSecurityContext.runAsNonRoot`          | Run as non-root            | `true`  |
| `podSecurityContext.fsGroup`               | File system group          | `0`     |
| `securityContext.allowPrivilegeEscalation` | Allow privilege escalation | `false` |
| `securityContext.runAsNonRoot`             | Run as non-root            | `true`  |

## Examples

> **Note:** All installation examples below require authentication credentials (`--set auth.username=... --set auth.password=...`) unless `--set auth.enabled=false` is specified.

### Basic Installation

Authentication is enabled by default and requires credentials:

```bash
helm install s4 ./charts/s4 --namespace s4 --create-namespace \
  --set auth.username=admin \
  --set auth.password=your-secure-password
```

### Without Authentication

To disable authentication:

```bash
helm install s4 ./charts/s4 --namespace s4 --create-namespace \
  --set auth.enabled=false
```

### With OpenShift Route (Default)

OpenShift Route is enabled by default for the Web UI. To customize the hostname:

```bash
helm install s4 ./charts/s4 --namespace s4 --create-namespace \
  --set route.host=s4.apps.example.com
```

### With Kubernetes Ingress

```bash
helm install s4 ./charts/s4 --namespace s4 --create-namespace \
  --set ingress.enabled=true \
  --set "ingress.hosts[0].host=s4.example.com" \
  --set "ingress.hosts[0].paths[0].path=/" \
  --set "ingress.hosts[0].paths[0].pathType=Prefix"
```

### Exposing the S3 API Externally

By default, only the Web UI is exposed externally. The S3 API (port 7480) is only accessible within the cluster. To also expose the S3 API for use with `aws s3`, `mc`, or other S3-compatible clients:

**OpenShift:**

```bash
helm install s4 ./charts/s4 --namespace s4 --create-namespace \
  --set route.s3Api.enabled=true \
  --set route.s3Api.host=s3.s4.apps.example.com
```

**Kubernetes Ingress:**

```bash
helm install s4 ./charts/s4 --namespace s4 --create-namespace \
  --set ingress.s3Api.enabled=true \
  --set "ingress.s3Api.hosts[0].host=s3.s4.example.com" \
  --set "ingress.s3Api.hosts[0].paths[0].path=/" \
  --set "ingress.s3Api.hosts[0].paths[0].pathType=Prefix"
```

### With Custom Storage

```bash
helm install s4 ./charts/s4 --namespace s4 --create-namespace \
  --set storage.data.size=100Gi \
  --set storage.data.storageClass=fast-storage
```

### With Local File Browser

Local storage is disabled by default. To enable local file browsing:

```bash
helm install s4 ./charts/s4 --namespace s4 --create-namespace \
  --set storage.localStorage.enabled=true \
  --set storage.localPaths=/opt/app-root/src/data \
  --set storage.localStorage.size=500Gi \
  --set storage.localStorage.storageClass=fast-storage
```

### Using Existing PVCs

```bash
helm install s4 ./charts/s4 --namespace s4 --create-namespace \
  --set storage.data.existingClaim=my-existing-data-pvc
```

For local storage with an existing PVC:

```bash
helm install s4 ./charts/s4 --namespace s4 --create-namespace \
  --set storage.localStorage.enabled=true \
  --set storage.localPaths=/opt/app-root/src/data \
  --set storage.localStorage.existingClaim=my-existing-storage-pvc
```

### Using Existing Secret

```bash
# Create secret first
kubectl create secret generic my-s4-credentials \
  --from-literal=AWS_ACCESS_KEY_ID=mykey \
  --from-literal=AWS_SECRET_ACCESS_KEY=mysecret \
  -n s4

# Install using existing secret
helm install s4 ./charts/s4 --namespace s4 \
  --set s3.existingSecret=my-s4-credentials
```

## Upgrading

```bash
# Upgrade with new values
helm upgrade s4 ./charts/s4 --namespace s4 -f my-values.yaml

# Upgrade to new chart version
helm upgrade s4 ./charts/s4 --namespace s4 --reuse-values
```

## Accessing S4

S4 exposes two endpoints:

| Endpoint   | Port | Purpose                                                    |
| ---------- | ---- | ---------------------------------------------------------- |
| **Web UI** | 5000 | Browser-based storage management interface                 |
| **S3 API** | 7480 | S3-compatible API for `aws s3`, `mc`, and other S3 clients |

### Port Forward (Development)

```bash
kubectl port-forward svc/s4 5000:5000 7480:7480 -n s4
```

Then access:

- **Web UI**: http://localhost:5000
- **S3 API**: http://localhost:7480

### NodePort

If `service.nodePort.enabled=true`, both endpoints are accessible via node IP and assigned ports.

### Ingress / Route

By default, only the **Web UI** is exposed externally via Ingress or OpenShift Route. The **S3 API** remains cluster-internal.

To also expose the S3 API externally, enable the separate S3 API Ingress or Route (see [Exposing the S3 API Externally](#exposing-the-s3-api-externally) above). Each endpoint gets its own hostname, for example:

- **Web UI**: `https://s4.apps.example.com`
- **S3 API**: `https://s3.s4.apps.example.com`

## Troubleshooting

### Check Pod Status

```bash
kubectl get pods -n s4 -l app.kubernetes.io/name=s4
kubectl logs -n s4 -l app.kubernetes.io/name=s4
```

### Check Events

```bash
kubectl get events -n s4 --sort-by='.lastTimestamp'
```

### Verify Configuration

```bash
kubectl get configmap -n s4 -l app.kubernetes.io/name=s4 -o yaml
kubectl get secret -n s4 -l app.kubernetes.io/name=s4
```
