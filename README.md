# Automate Edge Network Remediation with AI

Detect, diagnose, and remediate failures across distributed edge clusters using AI-driven root cause analysis and automated playbooks.

![image](https://img.shields.io/badge/OpenShift-4.21+-red)
![image](https://img.shields.io/badge/OpenShift%20AI-3.3+-red)
![image](https://img.shields.io/badge/Granite-4.0-purple)
![image](https://img.shields.io/badge/LangGraph-1.0+-blue)
![image](https://img.shields.io/badge/License-Apache%202.0-blue.svg)

## Table of Contents

- [Overview](#overview)
- [Detailed description](#detailed-description)
  - [Architecture diagrams](#architecture-diagrams)
- [Requirements](#requirements)
  - [Minimum hardware requirements](#minimum-hardware-requirements)
  - [Minimum software requirements](#minimum-software-requirements)
  - [Required user permissions](#required-user-permissions)
- [Deploy](#deploy)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Validating the deployment](#validating-the-deployment)
  - [Delete](#delete)
- [References](#references)
- [Tags](#tags)

## Overview

This quickstart helps network operations teams managing distributed edge infrastructure eliminate manual incident response. It provides an autonomous AI agent that detects failures, identifies root causes, and executes remediation playbooks across multiple OpenShift clusters without human intervention.

## Detailed description

Network operations teams managing several edge sites face a growing challenge: manual incident response cannot keep pace. Tickets arrive, engineers escalate, investigate, and execute playbooks by hand. Time-to-resolution takes a significant amount of time for routine faults, alert fatigue grows from unstructured logs, and expert knowledge is required across multiple domains.

This quickstart deploys an AI-driven operations agent that inverts that workflow. It streams logs in real time from edge clusters to a central hub, runs root cause analysis using IBM Granite 4.0 and RAG-grounded runbooks, and automatically executes Ansible remediation playbooks. When AI cannot resolve an issue, it escalates to ServiceNow and notifies teams via Slack. Every decision is traced end-to-end through Langfuse for compliance and learning.

### Architecture diagrams

For deployment modes, architecture details, and technical deep dive, see the [Architecture Guide](docs/architecture.md).

The solution consists of three layers:

- **AI & LLM:** Red Hat OpenShift AI 3.3, IBM Granite 4.0, LangGraph 1.0
- **Automation:** Ansible Automation Platform 2.5, Advanced Cluster Management 2.15
- **Data & Observability:** Red Hat Streams for Apache Kafka 3.1, PostgreSQL + pgvector, Langfuse 3.x

## Requirements

### Minimum hardware requirements

- **CPU:** 8+ vCPU cores
- **Memory:** 32+ GB RAM
- **GPU:** 1x NVIDIA GPU (A10G, A100, L40S, or equivalent) for Granite model serving. Not required if using an external model endpoint (MaaS).
- **Storage:** 100+ GB available disk space (storage class `gp3-csi` or override in Helm values)

For hub-spoke mode, each edge cluster requires OpenShift 4.21+ (SNO supported) with 16+ GB RAM and TLS-secured Kafka connectivity to the hub.

### Minimum software requirements

**Platform:**

- OpenShift 4.21+
- Helm 3+

**CLI tools:**

| Tool | Purpose |
|------|---------|
| `oc` | OpenShift CLI, cluster login, resource inspection, port-forwards |
| `helm` (v3+) | Chart install, uninstall, dependency management |
| `podman` | Container image builds and pushes |
| `make` | Build orchestration (all targets in the root `Makefile`) |
| `jq` | JSON processing (used by Langfuse secrets script) |
| `openssl` | Key generation for Langfuse secrets |

**Required OpenShift Operators:**

| Operator | Version |
|----------|---------|
| Red Hat OpenShift AI | 3.3+ |
| Red Hat Ansible Automation Platform | 2.5+ |
| Llama Stack K8s Operator | latest |
| Advanced Cluster Management | 2.15+ |
| OpenShift Logging + Loki Operator | 6.4+ |
| Ansible Automation Platform Operator | 2.5+ |

**AAP controller setup:**

By default, `make helm-install` deploys an AAP mock. To use a real AAP controller, set `ENABLE_AAP_MOCK=false` and provide an OAuth2 token via `AAP_TOKEN` or reference a pre-existing secret via `AAP_SECRET_NAME`.

The controller API prefix defaults to `/api/controller/v2` (AAP 2.5+ gateway). Ensure the controller is enabled in the `AnsibleAutomationPlatform` CR (`spec.controller.disabled: false`).

For AAP operator installation on OpenShift, see the [AAP Operator Installation Guide](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.5/html-single/installing_on_openshift_container_platform/index).

**AAP authentication:**

The MCP AAP server authenticates using an API token. Create one in the AAP web UI:

1. Navigate to **Access Management > OAuth Applications > Create OAuth Application**. Set a name (e.g., `noc-bot`), select your organization, and set **Authorization grant type** to `Resource owner password-based` with **Client type** `Confidential`. Save the Client ID and Client Secret; they are only displayed once.
2. Navigate to **Access Management > API Tokens > Create token**. Select the OAuth application created above (e.g., `noc-bot`), set **Scope** to `Write`.
3. Copy the token and refresh token immediately. They are only displayed once and cannot be retrieved after closing the dialog. Tokens expire after 1 year by default. To renew, create a new token from the AAP UI or use the refresh token with the Client ID/Secret via the AAP `/o/token/` endpoint.

The token's user must have the following [RBAC roles](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.5/html/access_management_and_authentication/gw-managing-access):

| Role | Reason |
|------|--------|
| **Admin** on Job Templates | Create, copy, and patch job templates (`upsert_job_template`) |
| **Execute** on Job Templates | Launch job templates (`launch_job`) |
| **Use** on Projects, Inventories, Credentials | Required when creating templates that reference these objects |

**AAP project and job template:**

1. Navigate to **Automation Execution > Projects > Create project**:
   - **Name**: `ai-driven-network-remediation`
   - **Organization**: select your organization
   - **Source control type**: `Git`
   - **Source control URL**: `https://github.com/rh-ai-quickstart/ai-driven-network-remediation.git`
   - **Source control branch/tag/commit**: `main` (or leave blank for default)
   - Under **Options**, check **Update revision on launch**
2. Navigate to **Automation Execution > Templates > Create template > Create job template**:
   - **Name**: `lightspeed-runner`
   - **Job type**: `Run`
   - **Inventory**: `Demo Inventory` (or your own inventory)
   - **Project**: `ai-driven-network-remediation`
   - **Playbook**: select any playbook (the agent will override this per generated playbook)
   - Under **Variables**, check **Prompt on launch**

A second AAP project (`lightspeed-generated`) is created automatically by the Helm credential job, pointing at the Gitea repository where generated playbooks are stored. The agent commits each generated playbook to Gitea, syncs this project, then upserts a job template referencing the committed file.

Provide the AAP API token at deploy time:

```bash
ENABLE_AAP_MOCK=false AAP_TOKEN=<token> make helm-install
```

Or create a K8s secret and reference it:

```bash
oc create secret generic my-aap-secret \
  --from-literal=AAP_TOKEN='<token>' \
  -n <namespace>
ENABLE_AAP_MOCK=false AAP_SECRET_NAME=my-aap-secret make helm-install
```

If the secret is in a different namespace, copy it first:

```bash
oc get secret my-aap-secret -n aap -o json | \
  jq 'del(.metadata.namespace,.metadata.resourceVersion,.metadata.uid,.metadata.creationTimestamp)' | \
  oc apply -n <target-namespace> -f -
```

**Cluster credentials for Lightspeed playbooks:**

The `lightspeed-runner` template requires Kubernetes credentials so generated playbooks can patch resources on target clusters. In both modes, credentials are attached to the template at deploy time. The agent service's `launch_job` call only passes `job_template_name` and `extra_vars` (including `edge_site_id`). Templates created by `upsert_job_template` (copied from `lightspeed-runner`) inherit credentials automatically.

**Single-cluster mode (default, development only)**

When `aapCredential.enabled=true` (set automatically when `ENABLE_AAP_MOCK=false`), the Helm chart creates a ServiceAccount with broad RBAC permissions (get/list/patch across core, apps, batch, and networking API groups), registers it as an AAP credential (`hub-remediation`), and attaches it to the `lightspeed-runner` template. This is intended for development where AAP and the target workloads share the same cluster.

#### RHACM multicluster (ACM hub proxy)

For environments with Red Hat Advanced Cluster Management (RHACM 2.9+), a single credential routes playbooks to any managed cluster through the ACM cluster proxy. The Helm chart creates a custom AAP credential type that injects `hub_url` and `token_acm` as extra vars, and attaches the credential to the `lightspeed-runner` template. Generated playbooks use `ansible.builtin.uri` for Kubernetes API calls and construct the K8s API URL as `hub_url/edge_site_id`, routing all calls through the ACM cluster proxy.

Prerequisites (on the ACM hub cluster):
1. Enable the ManagedServiceAccount addon
2. Create a ServiceAccount with cluster-proxy access
3. Get the cluster proxy URL and a hub token

```bash
# Get cluster proxy URL
oc get route -n multicluster-engine cluster-proxy-addon-user \
  -o jsonpath='https://{.spec.host}'

# Generate hub token (valid for 1 year)
oc create token <service-account> -n <namespace> --duration=8760h
```

Deploy with multicluster enabled:

```bash
make helm-install \
  ENABLE_AAP_MOCK=false \
  AAP_TOKEN=<token> \
  ENABLE_MULTICLUSTER=true \
  CLUSTER_PROXY_URL=<proxy-url> \
  RHACM_HUB_TOKEN=<sa-token>
```

For a detailed guide, see [Multicluster authentication for Ansible Automation Platform](https://developers.redhat.com/articles/2025/09/08/multicluster-authentication-ansible-automation-platform).

**Ansible Lightspeed setup:**

Ansible Lightspeed (the intelligent assistant chatbot) is required for playbook generation. Deploy the AAP operator and enable Lightspeed in the `AnsibleAutomationPlatform` CR (`spec.lightspeed.disabled: false`). For installation instructions, see the [AAP on OpenShift documentation](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.5/html/installing_on_openshift_container_platform/deploying-chatbot-operator).

Set `LIGHTSPEED_URL` to the chatbot service endpoint. The Helm chart supports referencing an external API key secret via `tokenSecretName`, or uses a manually provided `LIGHTSPEED_TOKEN` for authentication against the ALS API.

For development only, Lightspeed can be disabled with `ENABLE_LIGHTSPEED=false`.

**AI model endpoint:**

An OpenAI-compatible LLM endpoint is required. Set these environment variables before deploying:

```bash
export ADNR_LLM_ID=granite-3-2-8b-instruct
export ADNR_LLM_URL=https://your-llm-endpoint.example.com/v1
export ADNR_LLM_TOKEN=your-llm-api-token
```

See `.env.example` for the full configuration template.

### Required user permissions

This quickstart requires **cluster-admin** access to:

- Install OpenShift Operators (RHOAI, AAP, ACM, Lightspeed, Logging)
- Create ClusterRoleBindings for edge RBAC and Lightspeed service accounts
- Deploy Helm charts that create cluster-scoped resources

For hub-spoke mode, admin access is also required on each edge cluster.

## Deploy

### Prerequisites

- Access to a Red Hat OpenShift 4.21+ cluster with all [required operators](#minimum-software-requirements) installed
- `oc` CLI authenticated to the cluster (`oc login`)
- `helm` CLI (v3+) installed
- AI model endpoint credentials (`ADNR_LLM_ID`, `ADNR_LLM_URL`, `ADNR_LLM_TOKEN`)

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/rh-ai-quickstart/ai-driven-network-remediation.git
   cd ai-driven-network-remediation
   ```

2. Deploy the solution:

   ```bash
   make helm-install
   ```

   Single-cluster and ACM hub-spoke orchestration use `make acm-deploy` (`CLUSTER_COUNT=1` or `>=2`). See the [Multi-cluster deployment guide](docs/multi-cluster-deploy.md).

   With Langfuse observability (optional):

   ```bash
   ENABLE_LANGFUSE=true make helm-install
   ```

   See [Langfuse Deployment Guide](docs/langfuse-deploy.md) for Langfuse details.

### Validating the deployment

1. Check all pods are running:

   ```bash
   oc get pods -n $NAMESPACE
   ```

2. Get the frontend URL:

   ```bash
   echo "https://$(oc get route noc-frontend -n $NAMESPACE --template='{{.spec.host}}')"
   ```

### Delete

Single-cluster (`CLUSTER_COUNT=1`):

```bash
make helm-uninstall
# or: make acm-teardown
```

Hub + spokes (`CLUSTER_COUNT>=2`): tear down ArgoCD edge apps, ACM policy, spoke
`dark-noc-edge` namespaces, then the hub chart:

```bash
CLUSTER_COUNT=2 make acm-teardown
```

Full topology, naming, verify, and teardown notes: [Multi-cluster deployment guide](docs/multi-cluster-deploy.md).

Verify removal:

```bash
oc get pods -n $NAMESPACE
# Should return "No resources found" (or namespace gone)
# On each spoke: oc get ns dark-noc-edge  (should be NotFound)
```

## References

- [IBM Granite Model Documentation](https://www.ibm.com/granite)
- [Red Hat OpenShift AI](https://www.redhat.com/en/technologies/cloud-computing/openshift/openshift-ai)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Architecture Guide](docs/architecture.md)
- [Multi-cluster deployment guide](docs/multi-cluster-deploy.md)
- [Fast-path healer demo script](docs/FAST-PATH-HEALER-DEMO-SCRIPT.md)

## Tags

- **Title:** Automate Edge Network Remediation with AI
- **Description:** Detect, diagnose, and remediate failures across distributed edge clusters using AI-driven root cause analysis and automated playbooks.
- **Industry:** Telecommunications
- **Product:** OpenShift AI
- **Use case:** Automation, network operations
- **Partner:** N/A
- **Contributor org:** Red Hat

