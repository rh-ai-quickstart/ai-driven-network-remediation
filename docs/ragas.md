# RAGAS Evaluation

Evaluate the quality of the NOC runbook RAG pipeline using [RAGAS](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.4/html/evaluating_ai_systems/evaluating-rag-systems-with-ragas_evaluate) (Retrieval-Augmented Generation Assessment) metrics through the Llama Stack evaluation API.

## Overview

RAGAS is integrated as an **inline provider** in the AutoRAG Llama Stack distribution. It measures:

- **Answer Relevancy** — Is the generated answer relevant to the question?
- **Faithfulness** — Is the answer grounded in the retrieved context?
- **Context Precision** — Are the retrieved documents relevant?
- **Context Recall** — Is all necessary information present in the retrieved context?
- **Answer Correctness** — How accurate is the answer compared to ground truth?

## Prerequisites

- The project is deployed with `make helm-install` (runbooks ingested, vector store populated)
- `ADNR_LLM_ID`, `ADNR_LLM_URL`, `ADNR_LLM_TOKEN` are set (the inference model is used by RAGAS to evaluate answer quality)
- TrustyAI component is `Managed` in the DataScienceCluster (provides the RAGAS evaluation provider)

## How it works

1. The AutoRAG `LlamaStackDistribution` is deployed with `TRUSTYAI_EMBEDDING_MODEL` set, which enables the RAGAS inline provider
2. The ingestion pipeline exposes a `POST /evaluate` endpoint that:
   - Accepts evaluation cases (questions + reference answers from `hub/autorag/test-data.json`)
   - Searches the vector store for retrieved contexts
   - Calls the inference model to generate responses
   - Submits the assembled data to the Llama Stack RAGAS evaluation API
   - Returns metric scores

## Quick start

```bash
# Ensure the project is deployed and ingestion has run
make helm-install
# POST /runbooks/sync and /runbooks/ingest if not already done

# Run RAGAS evaluation with the built-in test data
make ragas-evaluate
```

## API

### `POST /evaluate`

Run a RAGAS evaluation against the deployed RAG pipeline.

**Request body:**

```json
{
  "scoring_functions": ["answer_relevancy", "faithfulness"],
  "data": [
    {
      "question": "How do I check consumer group lag?",
      "correct_answers": ["Run kafka-consumer-groups.sh with --describe"]
    }
  ]
}
```

- `scoring_functions` (optional): list of RAGAS metrics to compute. Default: `["answer_relevancy"]`. Available: `answer_relevancy`, `faithfulness`, `context_precision`, `context_recall`, `answer_correctness`.
- `data` (required): evaluation cases. Supports two formats:
  - **Project format**: `{question, correct_answers}` — the endpoint retrieves contexts from the vector store and generates LLM responses automatically.
  - **RAGAS format**: `{user_input, response, retrieved_contexts, reference}` — pre-prepared evaluation data submitted directly.

**Response:**

```json
{
  "scores": {
    "answer_relevancy": {
      "aggregated": {"answer_relevancy": 0.89},
      "per_row": [{"score": 0.97}, {"score": 0.81}]
    }
  },
  "row_count": 2
}
```

## Configuration

| Environment variable | Helm value | Description |
|---|---|---|
| `RAGAS_INFERENCE_MODEL` | `ingestionPipeline.ragas.inferenceModel` | Inference model ID for RAGAS evaluation (set automatically from `ADNR_LLM_ID`) |
| `TRUSTYAI_EMBEDDING_MODEL` | `ragas.embeddingModel` (AutoRAG chart) | Embedding model used by RAGAS for semantic similarity (`all-MiniLM-L6-v2`) |

## Test data

The project includes 30 evaluation cases in `hub/autorag/test-data.json`, covering all 10 runbooks (3 questions each). These are the same cases used by the existing retrieval e2e tests in `hub/integration-tests/tests/autorag/test_rag_e2e.py`.
