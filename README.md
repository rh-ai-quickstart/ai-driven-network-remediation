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

1. Uninstall the Helm release:

   ```bash
   make helm-uninstall
   ```

2. Verify removal:

   ```bash
   oc get pods -n $NAMESPACE
   # Should return "No resources found"
   ```

## References

- [IBM Granite Model Documentation](https://www.ibm.com/granite)
- [Red Hat OpenShift AI](https://www.redhat.com/en/technologies/cloud-computing/openshift/openshift-ai)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Architecture Guide](docs/architecture.md)

## Tags

- **Title:** Automate Edge Network Remediation with AI
- **Description:** Detect, diagnose, and remediate failures across distributed edge clusters using AI-driven root cause analysis and automated playbooks.
- **Industry:** Telecommunications
- **Product:** OpenShift AI
- **Use case:** Automation, network operations
- **Partner:** N/A
- **Contributor org:** Red Hat

