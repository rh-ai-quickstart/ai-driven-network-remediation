# Architecture Guide

## Solution Stack

**AI & LLM:**

- Red Hat OpenShift AI (RHOAI) 3.3 - MLOps platform
- IBM Granite 4.0 - Generative AI for log analysis
- LangGraph 1.0 - Agentic workflow orchestration

**Automation:**

- Red Hat Ansible Automation Platform (AAP) 2.5
- Event-Driven Ansible (EDA) - Kafka-triggered playbooks
- Advanced Cluster Management (ACM) 2.15 - Multi-cluster governance

**Data & Observability:**

- Red Hat Streams for Apache Kafka 3.1 - Event streaming
- PostgreSQL + pgvector - Vector embeddings for RAG
- Langfuse 3.x - LLM observability & tracing
- OpenShift Logging - Log aggregation

## Deployment Modes

### Single-Cluster (Development)

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

Use for development, testing, and proof-of-concept.

### Hub-Spoke (Production)

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
           |  Kafka TLS
           |  ACM Management
           |  AAP API
           v
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

Use for production edge operations across multiple sites.

## AI Analysis Workflow

See [Graph Nodes](graph-nodes.md) for the full pipeline diagram and per-node reference.

## Data Persistence

- **Incident state** - PostgreSQL (LangGraph checkpoint)
- **Runbooks** - MinIO object storage + PostgreSQL/pgvector (RAG)
- **Traces** - Langfuse (observability)
- **Playbook definitions** - AAP (Ansible)
- **Logs** - Kafka (event stream)

## Multi-Cluster Coordination

**Hub Cluster (Hub-Spoke Mode):**

- Receives logs from all edge sites via Kafka TLS
- Runs AI analysis
- Triggers AAP playbooks
- Manages access to edge clusters via ACM

**Edge Clusters:**

- Collect logs from monitored workloads
- Stream to hub via Kafka
- Edge fast-path healer (watcher + runner) for known-safe OOM restart on spoke. Demo script: [FAST-PATH-HEALER-DEMO-SCRIPT.md](FAST-PATH-HEALER-DEMO-SCRIPT.md)
- Execute remediation playbooks via AAP
- Report status back to hub
