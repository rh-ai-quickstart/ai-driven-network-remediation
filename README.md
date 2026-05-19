# AI Driven Network Remediation

> AI-Driven Self-Healing for Distributed Edge Infrastructure

![image](https://img.shields.io/badge/OpenShift-4.21+-red) 
![image](https://img.shields.io/badge/OpenShift%20AI-3.3+-red) 
![image](https://img.shields.io/badge/Granite-4.0-purple) 
![image](https://img.shields.io/badge/LangGraph-1.0+-blue) 
![image](https://img.shields.io/badge/License-Apache%202.0-blue.svg)

- - -
## Table of Contents

- [Overview](#overview)
- [The Problem](#the-problem)
- [Our Solution](#our-solution)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Deployment Modes](#deployment-modes)
- [Usage](#usage)
- [Validation](#validation)
- [Cleanup](#cleanup)
- [References](#references)

- - -
## Overview

The **AI-Driven Network Remediation** quickstart is an AI-driven network
operations solution for distributed edge infrastructure. It autonomously
detects failures at edge sites, performs deep root cause analysis using IBM
Granite 4.0 AI models, and executes Ansible remediation playbooks—all without
human intervention. When AI cannot resolve an issue, it automatically creates
tickets and notifies teams.

### Key Capabilities


|Capability              |Technology                    |Result                         |
|------------------------|------------------------------|-------------------------------|
|Real-time log streaming |Red Hat Streams for Kafka 3.1 |< 1s edge → hub                |
|AI log analysis         |IBM Granite 4.0 + RHOAI 3.3   |< 5s root cause analysis       |
|RAG-grounded decisions  |LlamaStack + pgvector         |Runbook-based remediation      |
|Automated remediation   |AAP 2.5 + Event-Driven Ansible|< 30s MTTR                     |
|Multi-cluster management|ACM 2.15                      |Hub controls edge fleet        |
|Full observability      |Langfuse 3.x                  |Every AI decision traced       |
|Human-in-the-loop       |LangGraph 1.0                 |Approval gates for P1 incidents|

- - -
## The Problem

**Manual incident response is slow and error-prone:**

- 📋 Tickets arrive → engineers escalate → investigate → execute playbooks
- ⏱️ Time-to-resolution: 30+ minutes for routine faults
- 😫 Alert fatigue from unstructured logs
- 🌐 Distributed edge sites compound complexity
- 👤 Requires expert knowledge across multiple domains

**At scale (100+ edge sites), this becomes impossible.**

- - -
## Our Solution

**AI-Driven Network Remediation** inverts the workflow:

1.  **Detect** — Real-time log streaming from edge → hub
2.  **Analyze** — AI-driven root cause analysis in < 5 seconds
3.  **Remediate** — Automated playbook execution via Ansible Automation Platform
4.  **Escalate** — ServiceNow tickets + Slack for unresolved cases
5.  **Trace** — Every decision logged for compliance & learning

The result: **< 30 second MTTR** for known failure patterns, powered by Granite
4.0 and LangGraph.

- - -
## Architecture

### Solution Stack

**AI & LLM:**

- Red Hat OpenShift AI (RHOAI) 3.3 — MLOps platform
- IBM Granite 4.0 — Generative AI for log analysis
- LangGraph 1.0 — Agentic workflow orchestration

**Automation:**

- Red Hat Ansible Automation Platform (AAP) 2.5
- Event-Driven Ansible (EDA) — Kafka-triggered playbooks
- Advanced Cluster Management (ACM) 2.15 — Multi-cluster governance

**Data & Observability:**

- Red Hat Streams for Apache Kafka 3.1 — Event streaming
- PostgreSQL + pgvector — Vector embeddings for RAG
- Langfuse 3.x — LLM observability & tracing
- OpenShift Logging — Log aggregation

### Deployment Modes

#### Mode 1: Single-Cluster (Development)

```
┌──────────────────────────────┐
│   OpenShift Cluster (OCP)    │
│                              │
│  ┌────────────────────────┐  │
│  │ AI Engine (RHOAI)      │  │
│  │ Kafka                  │  │
│  │ PostgreSQL + pgvector  │  │
│  │ Langfuse Observability │  │
│  │ AAP Automation         │  │
│  └────────────────────────┘  │
│                              │
│  ┌────────────────────────┐  │
│  │ Simulated Edge         │  │
│  │ (separate namespace)   │  │
│  └────────────────────────┘  │
│                              │
└──────────────────────────────┘
```
**Use for:** Development, testing, proof-of-concept

#### Mode 2: Hub-Spoke (Production)

```
┌──────────────────────────────┐
│   Hub Cluster (OCP)          │
│   (AI, Automation, Control)  │
│                              │
│  ┌────────────────────────┐  │
│  │ RHOAI + Granite        │  │
│  │ Kafka + PostgreSQL     │  │
│  │ Langfuse + AAP         │  │
│  │ ACM Hub                │  │
│  └────────────────────────┘  │
└──────────────────────────────┘
           ↑ Kafka TLS
           │ ACM Management
           │ AAP API
           ↓
┌──────────────────────────────┐
│   Edge Cluster (OCP SNO)     │
│   (Monitoring & Workloads)   │
│                              │
│  ┌────────────────────────┐  │
│  │ nginx + Workloads      │  │
│  │ Vector Log Collection  │  │
│  │ ACM Spoke              │  │
│  └────────────────────────┘  │
│                              │
└──────────────────────────────┘
```
**Use for:** Production edge operations, multiple sites

- - -
## Prerequisites

### Required Operators (OpenShift)

Install these operators on your cluster(s) before deploying:

- **Red Hat OpenShift AI 3.3** — For Granite model serving
- **Red Hat Streams for Apache Kafka 3.1** — For event streaming
- **Red Hat Ansible Automation Platform 2.5** — For remediation
- **Advanced Cluster Management 2.15** — For hub-spoke mode (optional)
- **OpenShift Logging 6.4** — For log collection

### Minimum Resource Requirements

**Single-Cluster Mode:**

- 1 OpenShift 4.21+ cluster
- GPU node (NVIDIA A10G or equivalent) for Granite model
- 32GB+ node memory
- 100GB+ storage for databases

**Hub-Spoke Mode:**

- Hub cluster: Same as single-cluster
- Edge cluster(s): OpenShift 4.21+ (SNO supported), 16GB+ RAM
- Network: TLS-secured Kafka connectivity between clusters

### Required Credentials & Configuration

1.  **OpenShift Access:**
  - Hub cluster API URL + admin token/credentials
  - Edge cluster API URL + admin token/credentials (for hub-spoke mode)
2.  **Ansible Automation Platform:**
  - AAP controller URL + API token
  - Project with playbooks ready
3.  **Optional Integrations:**
  - ServiceNow instance URL + API credentials
  - Slack workspace + bot token
  - Langfuse instance (or auto-provisioned)

- - -
## Quick Start

### 1\. Clone the Repository

```bash
git clone https://github.com/rh-ai-quickstart/ai-driven-network-remediation.git
cd ai-driven-network-remediation
```

### 2\. Deploy

```bash
# Core platform
make helm-install

# With Langfuse observability
ENABLE_LANGFUSE=true make helm-install
```

See [Langfuse Deployment Guide](docs/langfuse-deploy.md) for details.

- - -
## Architecture Deep Dive

### AI Analysis Workflow

```
1. INGEST (Kafka)
   └─ Raw log event arrives
   
2. CONTEXT (RAG)
   └─ pgvector retrieves relevant runbooks
   
3. ANALYZE (Granite 4.0 + LangGraph)
   └─ RootCauseAnalysis struct (xgrammar enforced)
   
4. DECIDE (LangGraph Router)
   ├─ If confidence > 0.8 → REMEDIATE
   ├─ If confidence < 0.7 → ESCALATE
   └─ Else → REQUEST APPROVAL
   
5. EXECUTE (AAP + MCP)
   └─ Playbook runs (< 30s typical)
   
6. NOTIFY
   ├─ Slack message
   ├─ ServiceNow ticket (if escalated)
   └─ Langfuse trace recorded
```
### Data Persistence

- **Incident state** → PostgreSQL (LangGraph checkpoint)
- **Runbooks** → MinIO object storage + PostgreSQL/pgvector (RAG)
- **Traces** → Langfuse (observability)
- **Playbook definitions** → AAP (Ansible)
- **Logs** → Kafka (event stream)

### Multi-Cluster Coordination

**Hub Cluster (Hub-Spoke Mode):**

- Receives logs from all edge sites via Kafka TLS
- Runs AI analysis
- Triggers AAP playbooks
- Manages access to edge clusters via ACM

**Edge Clusters:**

- Collect logs from monitored workloads
- Stream to hub via Kafka
- Execute remediation playbooks via AAP
- Report status back to hub

- - -
## References

- AI Driven Network Remediation Architecture
- Deployment Guide
- [IBM Granite Model Documentation](https://www.ibm.com/granite)
- 
  [Red Hat OpenShift AI](https://www.redhat.com/en/technologies/cloud-computing/openshift/openshift-ai)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Original Auto-Darknoc POC](https://github.com/msugur/auto-darknoc)

- - -
## Support

For issues, questions, or contributions:

- GitHub Issues: 
  [Report a bug](https://github.com/rh-ai-quickstart/ai-driven-network-remediation/issues)
- Discussions: 
  [Ask a question](https://github.com/rh-ai-quickstart/ai-driven-network-remediation/discussions)

- - -
## License

This project is licensed under the Apache 2.0 License. See [LICENSE](LICENSE)
for details.

- - -
*Red Hat AI Quickstarts · AI Driven Network Operations · 2026*

