# AutoRAG — Optimizing RAG for Network Remediation Runbooks

AutoRAG is an OpenShift AI (3.4) feature that automatically finds the best RAG configuration
(chunking strategy, embedding model, retrieval method) for your documents and use case.

> **Technology Preview:** AutoRAG is a TP feature in OpenShift AI 3.4.

## Prerequisites

| Requirement | Status |
|---|---|
| OpenShift AI 3.4+ with Llama Stack Operator enabled | `oc get datasciencecluster default-dsc -o jsonpath='{.spec.components.llamastackoperator.managementState}'` → `Managed` |
| Data Science Pipelines enabled | `spec.components.aipipelines.managementState: Managed` |
| Dashboard enabled | `spec.components.dashboard.managementState: Managed` |
| Foundation model endpoint (Granite/vLLM) | Set `ADNR_LLM_ID`, `ADNR_LLM_URL`, `ADNR_LLM_TOKEN` env vars |
| MinIO deployed | `make minio-install` (already part of `make helm-install`) |

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  OpenShift AI Dashboard                                  │
│  ┌────────────┐                                          │
│  │  AutoRAG   │── runs optimization pipeline (KFP) ──┐  │
│  └────────────┘                                       │  │
└───────────────────────────────────────────────────────┼──┘
                                                        │
  ┌─────────────────────────────────────────────────────┼──┐
  │  Namespace: ai-driven-network-remediation-itay      │  │
  │                                                     ▼  │
  │  ┌─────────────────────┐    ┌──────────────────────┐  │
  │  │ LlamaStackDistrib.  │    │  Milvus + etcd       │  │
  │  │  (adnr-autorag)     │───▶│  (vector storage)    │  │
  │  │  + sentence-trans.  │    └──────────────────────┘  │
  │  │  + Granite LLM      │                              │
  │  └─────────────────────┘                              │
  │           │                                            │
  │           ▼                                            │
  │  ┌──────────────────┐    ┌─────────────────────────┐  │
  │  │  pgvector         │    │  MinIO (runbooks S3)    │  │
  │  │  (metadata store) │    └─────────────────────────┘  │
  │  └──────────────────┘                                  │
  └────────────────────────────────────────────────────────┘
```

## Deploy AutoRAG Infrastructure

```bash
# Set your LLM endpoint credentials
export ADNR_LLM_ID="granite-3.3-8b-instruct"
export ADNR_LLM_URL="https://your-vllm-endpoint/v1"
export ADNR_LLM_TOKEN="your-token"

# Deploy everything (includes Milvus + LlamaStackDistribution)
ENABLE_AUTORAG=true make helm-install

# Or deploy AutoRAG components standalone
make milvus-install
make autorag-install
```

## Check Status

```bash
make autorag-status
```

## Prepare Test Data

AutoRAG needs a JSON test file with questions, expected answers, and source document IDs.
A pre-built test data file is available at `hub/autorag/test-data.json` covering the 10 network
remediation runbooks.

### Test Data Format

```json
[
  {
    "question": "What causes nginx pods to enter CrashLoopBackOff?",
    "correct_answers": ["Configuration syntax errors, missing config files, or OOM kills"],
    "correct_answer_document_ids": ["nginx-crashloop.md"]
  }
]
```

## Run AutoRAG Optimization

1. Open the **OpenShift AI Dashboard** → your project
2. Navigate to **AutoRAG** section
3. Click **Create optimization run**
4. Configure:
   - **Llama Stack connection**: `http://adnr-autorag:8321` (the LSD deployed above)
   - **Documents**: Upload from MinIO bucket or select the runbooks folder
   - **Test data**: Upload `hub/autorag/test-data.json`
   - **Optimization metric**: "Context correctness" (recommended for retrieval-focused RAG)
   - **Embedding model**: BAAI/bge-m3 (auto-discovered from the LSD)
   - **Foundation model**: Your Granite model (auto-discovered)
5. Click **Create run**

## Evaluate Results

After the run completes:

1. Review the **leaderboard** — patterns ranked by optimization metric
2. Compare **Sample Q&A** across patterns to verify answer quality
3. Note the best pattern's configuration:
   - Chunking method and parameters
   - Retrieval method (vector vs hybrid)
   - Number of chunks retrieved

## Apply Results to Ingestion Pipeline

After identifying the optimal configuration, update the ingestion pipeline's chunking parameters
in `hub/ingestion-pipeline/src/ingestion_pipeline/clients/llamastack.py`:

```python
def ingest_text(
    self,
    *,
    filename: str,
    content: str,
    attributes: dict[str, str | float | bool] | None = None,
    chunk_size_tokens: int = 800,   # ← update with AutoRAG best value
    chunk_overlap_tokens: int = 80, # ← update with AutoRAG best value
) -> VectorStoreFileSummary:
```

Then redeploy:

```bash
make helm-uninstall && make helm-install
```

## Teardown

```bash
make autorag-uninstall
make milvus-uninstall
```

## Limitations (Technology Preview)

- Only English language documents supported
- Remote Milvus only (inline Milvus not supported for AutoRAG)
- Max 3 foundation models and 2 embedding models per run
- Images in documents are not processed
- No OCR for PDF documents
