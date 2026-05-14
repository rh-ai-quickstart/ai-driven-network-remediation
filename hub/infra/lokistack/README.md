# LokiStack

Deploys LokiStack with MinIO storage and Grafana on OpenShift for centralized log collection and querying.

## Quick Start

```bash
helm install lokistack ./chart -n <namespace>
```

The chart auto-discovers the cluster domain and OAuth server URL at install time. No cluster-specific values needed.

## What Gets Deployed

- **LokiStack** (via Loki Operator) - log aggregation backend
- **MinIO** - S3-compatible storage with auto-created `loki` bucket
- **Grafana** - log visualization with OpenShift OAuth integration

## Access Grafana

```bash
oc get route grafana -n <namespace> -o jsonpath='{.spec.host}'
```

Login via "Sign in with OpenShift". OAuth users are assigned Admin role automatically.

To query logs, go to **Explore** and select the **Loki-Application** datasource:

```
{kubernetes_namespace_name="<namespace>"}
```

## Requirements

This chart requires **OpenShift 4.x** — it uses Routes (`route.openshift.io/v1`), OAuthClient (`oauth.openshift.io/v1`), and the Loki Operator from `redhat-operators`. It is not compatible with vanilla Kubernetes without disabling OAuth and Routes (`--set grafana.oauth.enabled=false --set grafana.route.enabled=false --set minio.routes.api.enabled=false --set minio.routes.ui.enabled=false`).

## Prerequisites

Your OpenShift user needs ClusterRoleBindings for Loki access:

```bash
oc adm policy add-cluster-role-to-user cluster-logging-application-reader <user>
oc adm policy add-cluster-role-to-user cluster-logging-infrastructure-reader <user>  # optional
```

## Forwarding Logs from Deployments

Logs from any pod in the cluster are automatically collected by OpenShift's log collector when a `ClusterLogForwarder` is configured. No sidecar or code change is needed - anything written to stdout/stderr is captured.

Create a `ClusterLogForwarder` that targets this LokiStack:

```yaml
apiVersion: observability.openshift.io/v1
kind: ClusterLogForwarder
metadata:
  name: instance
  namespace: <namespace>
spec:
  serviceAccount:
    name: logcollector
  outputs:
    - name: loki-app
      type: lokiStack
      lokiStack:
        target:
          name: logging-loki
          namespace: <namespace>
        authentication:
          token:
            from: serviceAccount
  pipelines:
    - name: app-logs
      inputRefs: [application]
      outputRefs: [loki-app]
    - name: infra-logs
      inputRefs: [infrastructure]
      outputRefs: [loki-app]
```

The `logcollector` service account needs the `collect-application-logs` and `collect-infrastructure-logs` cluster roles:

```bash
oc adm policy add-cluster-role-to-user collect-application-logs -z logcollector -n <namespace>
oc adm policy add-cluster-role-to-user collect-infrastructure-logs -z logcollector -n <namespace>
```

## Overrides

| Use case | Flags |
|----------|-------|
| Production (external S3) | `--set minio.enabled=false --set lokistack.size=1x.medium --set minio.endpoint=<url>` |
| CI/CD (no OAuth) | `--set grafana.oauth.enabled=false` |
| Skip operator install | `--set operators.loki.enabled=false --set operators.operatorGroup.enabled=false` |

## Uninstall

```bash
helm uninstall lokistack -n <namespace>
oc delete pvc -l app=minio -n <namespace>  # clean up storage
```
