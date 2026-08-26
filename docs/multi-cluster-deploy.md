# Multi-cluster deployment guide

Deploy AI-Driven Network Remediation (ADNR) on a single OpenShift cluster, or as an ACM hub with edge spokes. The entry point is `make acm-deploy`. Topology is controlled by `CLUSTER_COUNT`.

For single-cluster-only installs without ACM orchestration, see [manual-deploy.md](manual-deploy.md). For AAP playbook routing through the ACM cluster proxy, see [RHACM multicluster (ACM hub proxy)](../README.md#rhacm-multicluster-acm-hub-proxy) in the README. That flag is separate from topology (details below).

## Overview

| Role | When | What runs |
|------|------|-----------|
| **All-in-one** | `CLUSTER_COUNT=1` | Full hub chart on one cluster, plus a simulated edge namespace (`dark-noc-edge`) |
| **Hub** | `CLUSTER_COUNT>=2` | Agent, Kafka, LLM stack, MCP, chatbot, frontend on the hub only |
| **Spoke** | `CLUSTER_COUNT>=2` | Edge chart via ArgoCD: nginx demo workload, ClusterLogForwarder → hub Kafka, Kafka client certs |

```
CLUSTER_COUNT=1  →  single-cluster (0 spokes, 1 physical cluster)
CLUSTER_COUNT=2  →  hub + 2 spokes (edge-site-01, edge-site-02; 3 physical clusters)
CLUSTER_COUNT=N  →  hub + N spokes (N >= 2; N + 1 physical clusters)
```

Hub-spoke log path: spoke ClusterLogForwarder ships Warning/Error app logs to the hub Kafka external route (mTLS). Hub LokiStack stays hub-local.

## Two flags that look alike

Do not confuse these. They solve different problems.

| Flag | Controls | Default | Typical use |
|------|----------|---------|-------------|
| `CLUSTER_COUNT` | ADNR topology (single-cluster vs hub + N spokes) | `1` | Always set this for deploy/teardown |
| `ENABLE_MULTICLUSTER` | AAP credential type that routes K8s API calls through the ACM cluster proxy | `false` | Real AAP on a hub that remediates remote spokes |

`CLUSTER_COUNT>=2` deploys edge ADNR and per-spoke MCP kubeconfigs. It does **not** turn on AAP multicluster auth.

`ENABLE_MULTICLUSTER=true` needs `CLUSTER_PROXY_URL`, `RHACM_HUB_TOKEN`, and usually `ENABLE_AAP_MOCK=false`. You can use it with either topology mode when AAP should call spoke APIs through the hub proxy. See the README section linked above.

## Naming conventions

Three identifiers appear in different layers. Mixing them breaks demo triggers and MCP routing.

| Layer | Pattern | Example | Used by |
|-------|---------|---------|---------|
| ACM ManagedCluster | `edge-site-NN` | `edge-site-01` | Hive, ArgoCD destination, MCP kubeconfig secret suffix |
| Alert label `edge_site_id` | `edge-NN` | `edge-01` | Kafka payloads, demo trigger `site`, audit, incident movie |
| Kubernetes namespace | `dark-noc-edge` | same on every spoke | Edge chart, MCP default namespace |

Demo trigger: always use `site=edge-01` (not `edge-site-01`).

## Prerequisites

Complete this section before `make acm-deploy`. The hub chart also expects the broader operator set listed in the [README minimum software requirements](../README.md#minimum-software-requirements) (RHOAI, Llama Stack, optional AAP/Logging on the hub). Defaults use the AAP mock (`ENABLE_AAP_MOCK=true`), so a real AAP controller is optional for topology bring-up.

### Tools and access

- `oc` logged into the **hub** cluster (`oc whoami` succeeds)
- `helm` v3+, `make`, and `jq` on your PATH
- LLM env: `ADNR_LLM_ID`, `ADNR_LLM_URL`, `ADNR_LLM_TOKEN` (see [.env.example](../.env.example))
- **cluster-admin** (or equivalent) on the hub; for hub-spoke, rights to manage spoke namespaces via ACM/ArgoCD
- OpenShift **4.21+** on hub and spokes
- Custom images only: `podman` login to your `REGISTRY`

### Capacity (hub-spoke)

ADNR hub plus ACM, GitOps, and RHOAI is heavy. Lab experience: a hub with only **2** workers saturates under that stack. Prefer about **4** worker nodes on the hub (for example `m6i.xlarge` class) before installing operators. Spokes can be smaller (about **2** workers) because they only run the edge chart (nginx + ClusterLogForwarder).

See the README [minimum hardware requirements](../README.md#minimum-hardware-requirements) for baseline sizing.

### Hub operators (install before deploy)

Install on the **hub** in this order. Wait for each layer before starting the next.

1. **ACM:** MultiClusterHub until Ready (`local-cluster` Available)
2. **OpenShift GitOps:** Application + ApplicationSet CRDs present
3. **RHOAI 3.4:** DataScienceCluster with `llamastackoperator=Managed`

#### 1. ACM (MultiClusterHub)

Install the Advanced Cluster Management operator and create a `MultiClusterHub` instance. Wait until the hub reports Ready and the local hub cluster is Available:

```bash
oc get mch -A
# Expect the MultiClusterHub phase/status Ready

oc get managedcluster local-cluster
# Expect Available=True (ACM hub self-registration)
```

`make acm-prereq-check` also verifies core ACM CRDs (`ManagedCluster`, `Placement`, `ManagedClusterSet`).

#### 2. OpenShift GitOps (ArgoCD)

Install OpenShift GitOps so Application and ApplicationSet APIs exist (ApplicationSet is required for edge fan-out):

```bash
oc get crd applications.argoproj.io
oc get crd applicationsets.argoproj.io
```

Enable ACM GitOps integration so imported spokes appear as ArgoCD cluster destinations. If Applications stay Pending, check ArgoCD cluster secrets for each `edge-site-NN`.

#### 3. RHOAI 3.4 (Llama Stack)

Install Red Hat OpenShift AI **3.4** (or newer in the 3.x line that supports Llama Stack). Configure the `DataScienceCluster` so the Llama Stack operator is Managed:

```bash
oc get datasciencecluster -A
oc get datasciencecluster default-dsc \
  -o jsonpath='{.spec.components.llamastackoperator.managementState}{"\n"}'
# Expect: Managed
```

(If your DSC name is not `default-dsc`, substitute the name from `oc get datasciencecluster -A`.)

Without `llamastackoperator=Managed`, hub helm-install cannot register the ADNR LLM / RAG stack that the agent depends on.

### Hub-spoke: spokes and logging

For `CLUSTER_COUNT>=2`, also complete:

- **`GITOPS_REPO_URL` and `GITOPS_REVISION`** set (Makefile defaults point at upstream `main`; use your feature branch until that branch merges)
- **N ManagedClusters Available** named `edge-site-01` .. `edge-site-NN`, **or** `CLUSTER_CREATE=true` with Hive credentials (optional path)
- **Import naming:** ACM ManagedCluster name must be `edge-site-NN`. Alert / demo `site` stays `edge-NN`.
- **OpenShift Logging on each spoke** (Cluster Logging operator, stable-6.x channel that provides the `ClusterLogForwarder` CRD). The edge chart CLF will not reconcile without it.
- **Optional (imported spokes):** seed admin kubeconfigs ACM/Hive-style helpers expect when the import path did not create them:

```bash
# Example for two imported spokes; paths are your spoke install kubeconfigs
oc create secret generic edge-site-01-admin-kubeconfig \
  --from-file=kubeconfig=/path/to/edge-01/auth/kubeconfig \
  -n edge-site-01
oc create secret generic edge-site-02-admin-kubeconfig \
  --from-file=kubeconfig=/path/to/edge-02/auth/kubeconfig \
  -n edge-site-02
```

Import existing spokes is the default (`CLUSTER_CREATE=false`). Hive provisioning is optional (see [Optional: provision spokes with Hive](#optional-provision-spokes-with-hive)).

### Quick prereq gate

After operators and spokes are ready:

```bash
make acm-prereq-check CLUSTER_COUNT=2
# CLUSTER_COUNT=1 skips ACM checks (exit 0)
```

## Key variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `CLUSTER_COUNT` | `1` | `1` = single-cluster; `>=2` = hub + that many spokes |
| `NAMESPACE` | `hub` | Hub install namespace |
| `EDGE_NAMESPACE` | `dark-noc-edge` | Edge namespace (sim or each spoke) |
| `SPOKE_NAME_PREFIX` | `edge-site` | ManagedCluster names: `edge-site-01`, … |
| `ACM_HUB_CLUSTER` | `local-cluster` | Hub ManagedCluster name (wired into GitOpsCluster) |
| `CLUSTER_CREATE` | `false` | Provision spokes with Hive when `true` |
| `GITOPS_REPO_URL` | upstream repo | ArgoCD source for `edge/helm` |
| `GITOPS_REVISION` | `main` | Branch/tag/commit for ArgoCD (use your feature branch before merge) |
| `KAFKA_EXTERNAL_HOST` | auto from route | Hub Kafka route host for spoke CLF; `acm-deploy` detects `kafka-external` if unset |
| `REGISTRY` / `VERSION` | Quay published images | Override for custom builds |
| `EDGE_SELF_HEAL` | `true` | ArgoCD selfHeal for edge apps; set `false` so AI remediation patches persist |
| `ENABLE_MULTICLUSTER` | `false` | AAP ACM proxy credential (not topology) |
| `multiClusterCreds.insecureSkipTlsVerify` | `true` (Helm) | Lab default for cluster-proxy kubeconfigs; set `false` to pin CA |

`DEPLOYMENT_MODE` and `SPOKE_COUNT` are derived by the Makefile. Do not set them by hand.

`NAMESPACE` and `EDGE_NAMESPACE` are substituted into ACM Placement / Policy / GitOpsCluster manifests at apply time. Keep the same values for teardown.

Offline / CI dry-runs: set `SKIP_OC_CHECK=1` so topology and ACM scripts skip live `oc` calls.

## Scenario A: Single cluster (`CLUSTER_COUNT=1`)

Uses the full hub chart and a simulated edge namespace (pause workload). No ACM spoke fan-out.

```bash
export ADNR_LLM_ID=... ADNR_LLM_URL=... ADNR_LLM_TOKEN=...
oc login --token=$TOKEN --server=https://$API:6443

CLUSTER_COUNT=1 make acm-deploy
make integration-tests
```

What ran:

1. `validate-topology` (spokeCount=0, deploymentMode=single-cluster)
2. `helm-install` (edgeRbac follows `ROUTES_ENABLED`)
3. `deploy-edge-workload` into `dark-noc-edge`

Checks:

```bash
oc get pods -n hub
oc get ns dark-noc-edge
oc get secret noc-openshift-edge-kubeconfig -n hub
```

Teardown:

```bash
CLUSTER_COUNT=1 make acm-teardown
# equivalent: make helm-uninstall
```

## Scenario B: Hub + spokes (`CLUSTER_COUNT=2`)

Example: hub plus two imported spokes. Replace `REGISTRY` / `VERSION` if you use custom images.

```bash
export ADNR_LLM_ID=... ADNR_LLM_URL=... ADNR_LLM_TOKEN=...
oc login --token=$TOKEN --server=https://$HUB_API:6443

# Optional: point ArgoCD at a branch that contains edge/helm before merge
export GITOPS_REPO_URL=https://github.com/rh-ai-quickstart/ai-driven-network-remediation.git
export GITOPS_REVISION=main

CLUSTER_COUNT=2 CLUSTER_CREATE=false make acm-deploy \
  REGISTRY=quay.io/rh-ai-quickstart \
  VERSION=0.1.5
```

Orchestration order when `CLUSTER_COUNT>=2`:

1. `acm-prereq-check` (ACM + ArgoCD CRDs, N Available ManagedClusters)
2. `acm-create-clusters` (no-op when `CLUSTER_CREATE=false`)
3. `acm-wait-for-clusters` (only when `CLUSTER_CREATE=true`)
4. `acm-label-spokes` (`adnr.io/role=edge` on each ManagedCluster)
5. Hub `helm-install` (`deploymentMode=hub-spoke`, `edgeRbac.enabled=false`)
6. `acm-distribute-kafka-certs` (`kafka-client-certs` in `dark-noc-edge` on each spoke)
7. ACM placement / namespace policy (`apply-placement.sh` substitutes `NAMESPACE` / `EDGE_NAMESPACE`)
8. `argocd-apply` + `argocd-wait-spokes` (edge chart Synced/Healthy)

### Verify spokes and GitOps

```bash
make acm-prereq-check CLUSTER_COUNT=2
oc get managedcluster -l adnr.io/role=edge
make argocd-wait-spokes CLUSTER_COUNT=2

# Expect Applications like adnr-edge-edge-site-01 / adnr-edge-edge-site-02
oc get applications.argoproj.io -A | grep adnr-edge
```

On each spoke (via ACM console, spoke kubeconfig, or MCP secret):

```bash
oc get deploy,pods -n dark-noc-edge
oc get secret kafka-client-certs -n dark-noc-edge
```

Expect nginx Ready, ClusterLogForwarder Running, and CLF labels with `edge_site_id: edge-01` / `edge-02`.

When `fastPathHealer.enabled=true` (default in the edge chart), also confirm:

```bash
oc get deploy -n dark-noc-edge | rg 'edge-nginx|fast-path'
oc logs -n dark-noc-edge deploy/edge-fast-path-runner --tail=20
```

See [edge-fast-path-healer.md](edge-fast-path-healer.md) for healer operations, and
[FAST-PATH-HEALER-DEMO-SCRIPT.md](FAST-PATH-HEALER-DEMO-SCRIPT.md) for the OOM dual-path demo.

Hub MCP secrets (per spoke):

```bash
oc get secrets -n hub | grep noc-openshift-kubeconfig-edge-site
```

### Demo trigger (E2E)

Use the **alert** site id (`edge-01`), not the ManagedCluster name.

```bash
oc port-forward svc/hub-chatbot-service 8080:80 -n hub
```

In another terminal:

```bash
curl -s -X POST localhost:8080/api/demo/trigger \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"oom","site":"edge-01"}'

curl -s localhost:8080/api/integrations | jq '.incident_movie'
```

Pass criteria:

- Hub agent logs show Kafka consume → graph run for the incident
- Incident movie / audit show `edge_site_id: edge-01` (title like `OOMKilled on edge-01`)
- MCP can reach `dark-noc-edge` on the matching spoke when investigation uses that site

## Scenario C: Teardown

Use the **same** `CLUSTER_COUNT` (and preferably the same `NAMESPACE` / `EDGE_NAMESPACE`) you used for deploy. A bare `make acm-teardown` defaults to `CLUSTER_COUNT=1` and will refuse to proceed if hub-spoke resources are still present.

```bash
CLUSTER_COUNT=2 make acm-teardown
```

Hub-spoke teardown removes ArgoCD edge apps (cascade-prune spoke resources), ACM placement/policy pieces, spoke `dark-noc-edge` namespaces (via GitOps/policy cleanup), then runs hub `helm-uninstall`.

Dry-run (no deletes, skips helm-uninstall):

```bash
CLUSTER_COUNT=2 make acm-teardown ACM_TEARDOWN_ARGS=--dry-run
```

Notes:

- When `CLUSTER_CREATE=false`, ManagedClusters themselves are **not** deleted. Only ADNR resources on them are torn down.
- When `CLUSTER_CREATE=true`, teardown also targets Hive ClusterDeployments for the rendered spokes.
- Single-cluster teardown skips ACM/ArgoCD steps and uninstalls the hub chart only.
- If you accidentally run teardown with `CLUSTER_COUNT=1` after a hub-spoke deploy, the script fails with a clear error instead of orphaning ArgoCD/ACM objects.

Confirm:

```bash
oc get pods -n hub
# Prefer: no resources (or namespace gone)

# On each spoke:
oc get ns dark-noc-edge
# Prefer: NotFound
```

## Optional: provision spokes with Hive

Default lab path imports clusters that already exist. To create spokes:

```bash
# Set Hive vars from .env.example (base domain, image set, AWS region, credentials)
export CLUSTER_CREATE=true
export HIVE_BASE_DOMAIN=...
export HIVE_CLUSTER_IMAGE_SET=...
export HIVE_AWS_REGION=...

CLUSTER_COUNT=2 CLUSTER_CREATE=true make acm-deploy
```

Dry-run ClusterDeployment manifests:

```bash
CLUSTER_COUNT=2 SKIP_OC_CHECK=1 make acm-create-clusters ACM_CREATE_ARGS=--dry-run
```

Hive bring-up can take a long time. Prefer importing Available ManagedClusters for demos unless you need full IPI.

## Troubleshooting

**Hub operators not Ready before deploy**  
Confirm MultiClusterHub Ready and `local-cluster` Available, GitOps Application + ApplicationSet CRDs, and DSC `llamastackoperator=Managed` (see [Hub operators](#hub-operators-install-before-deploy)).

**`acm-prereq-check` fails on ManagedClusters**  
Names must match rendered spokes (`edge-site-01`, …). Status must be Available. Labeling happens during deploy; prereq only checks presence and availability.

**ArgoCD Applications stuck / not created**  
Confirm spokes are registered as ArgoCD cluster secrets (ACM GitOps). Confirm `GITOPS_REVISION` contains `edge/helm`. Re-run:

```bash
make argocd-apply CLUSTER_COUNT=2
make argocd-wait-spokes CLUSTER_COUNT=2
```

**Spoke CLF cannot reach Kafka**  
Ensure route `kafka-external` exists in `hub`, or set `KAFKA_EXTERNAL_HOST` explicitly. Re-run cert distribution:

```bash
make acm-distribute-kafka-certs CLUSTER_COUNT=2
```

**Demo trigger shows wrong site / movie title**  
Use `site=edge-01`. `edge-site-01` is the ManagedCluster name only.

**MCP cannot talk to a spoke**  
Check `noc-openshift-kubeconfig-edge-site-NN` secrets in `hub` and that the multi-cluster creds job succeeded after hub install. Hub-spoke installs a per-spoke `ClusterPermission` that grants the hub MCP identity spoke API access through cluster-proxy. The binding subject must be the proxy User `cluster:hub:system:serviceaccount:hub:mcp-noc-openshift` (not a spoke ServiceAccount subject). Without it, proxy calls reach the spoke but return Forbidden. Confirm:

```bash
oc get clusterpermission -n edge-site-01
oc get secret noc-openshift-kubeconfig-edge-site-01 -n hub \
  -o jsonpath='{.data.kubeconfig}' | base64 -d | grep -E 'server:|tokenFile:'

MCP_POD=$(oc get pod -n hub -l app.kubernetes.io/name=noc-openshift \
  -o jsonpath='{.items[0].metadata.name}')
oc exec -n hub "$MCP_POD" -- \
  env KUBECONFIG=/kubeconfigs/edge-site-01/kubeconfig \
  oc --request-timeout=15s get ns dark-noc-edge
```

**Stale remediation patch persists after demo**  
Edge ArgoCD apps deploy with `selfHeal: true` by default. To let AI remediation patches (e.g. raised CPU limits) persist, redeploy with `EDGE_SELF_HEAL=false make argocd-apply`. To reset a spoke to its chart-declared state, redeploy with the default (`EDGE_SELF_HEAL=true`) or toggle it on the Application directly: `oc patch application adnr-edge-edge-site-01 -n openshift-gitops --type merge -p '{"spec":{"syncPolicy":{"automated":{"selfHeal":true}}}}'`.

**Cluster-proxy TLS skip**  
Per-spoke proxy kubeconfigs default to `insecure-skip-tls-verify: true` (`multiClusterCreds.insecureSkipTlsVerify`). That is a lab convenience. For non-lab hubs, set it to `false` and pin the cluster-proxy CA.

**Kafka cert ManifestWork**  
`acm-distribute-kafka-certs` can embed client cert material in a hub `ManifestWork` so ACM delivers the secret to spokes. Treat hub RBAC on `manifestworks` as carefully as Secrets; prefer etcd encryption at rest in shared labs.

**`ENABLE_MULTICLUSTER=true` errors at helm-install**  
That path needs `CLUSTER_PROXY_URL` and `RHACM_HUB_TOKEN`. It is unrelated to fixing missing spokes. Leave it `false` unless you are wiring real AAP through the ACM proxy.

**Hub helm --wait / LlamaStack Pending**  
The chart lowers LlamaStack CPU/memory requests so ACM hub workers can schedule during `helm --wait`. Raise `llama-stack.resources` if you have spare capacity. The `ranAnomalyDetector` deploys with the Telco/O-RAN use case (`ENABLE_TELCO_ORAN=true`, the default); disable it with `ENABLE_TELCO_ORAN=false` or `--set global.telcoOran.enabled=false`.

## Offline checks (no live ACM)

CI and local template validation:

```bash
make multi-cluster-template-tests
```

This runs topology validation for `CLUSTER_COUNT=1` and `2`, edge chart lint/template checks, and `pytest` under `hub/integration-tests/tests/multi_cluster/`.

## Related docs

- [Manual deploy](manual-deploy.md): hub chart without ACM orchestration
- [Architecture](architecture.md): system overview
- [README](../README.md): operators, AAP, Lightspeed, RHACM AAP proxy
- [.env.example](../.env.example): full variable template
