import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from ingestion_pipeline.clients.llamastack import (
    LlamaStackVectorStoreClient,
    VectorStoreFileContentSummary,
    VectorStoreFileSummary,
    VectorStoreSummary,
)
from ingestion_pipeline.clients.minio import MinioDocumentClient
from ingestion_pipeline.clients.ragas import RagasEvaluationClient
from ingestion_pipeline.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Ingestion Pipeline",
    description="Syncs packaged runbooks to MinIO and ingests them into a Llama Stack vector store",
    version="0.1.0",
)


def _get_client() -> LlamaStackVectorStoreClient:
    return LlamaStackVectorStoreClient(
        base_url=settings.llamastack_base_url,
        vector_store_name=settings.vector_store_name,
        embedding_model=settings.embedding_model,
        chunk_size_tokens=settings.chunk_size_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
    )


def _get_minio_client() -> MinioDocumentClient:
    if not settings.minio_is_configured:
        raise HTTPException(status_code=400, detail="MinIO is not fully configured")
    return MinioDocumentClient(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket=settings.minio_bucket,
        secure=settings.minio_secure,
    )


def _runbook_object_name(filename: str) -> str:
    prefix = settings.minio_runbook_prefix.strip("/")
    if not prefix:
        return filename
    return f"{prefix}/{filename}"


def _sync_packaged_runbooks_to_minio(minio_client: MinioDocumentClient) -> dict[str, Any]:
    minio_client.ensure_bucket()
    uploaded: list[str] = []
    skipped: list[str] = []
    if settings.runbooks_dir.exists():
        for runbook_path in sorted(settings.runbooks_dir.glob("*.md")):
            object_name = _runbook_object_name(runbook_path.name)
            was_uploaded = minio_client.put_text_object_if_missing(
                object_name,
                runbook_path.read_text(encoding="utf-8"),
            )
            if was_uploaded:
                uploaded.append(object_name)
            else:
                skipped.append(object_name)

    return {
        "bucket": settings.minio_bucket,
        "prefix": settings.minio_runbook_prefix,
        "uploaded_count": len(uploaded),
        "skipped_count": len(skipped),
        "uploaded_objects": uploaded,
        "skipped_objects": skipped,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/models")
def models() -> dict[str, Any]:
    client = _get_client()
    return {"models": client.list_models()}


@app.get("/vector-store")
def vector_store() -> dict[str, Any]:
    client = _get_client()
    summary: VectorStoreSummary = client.ensure_vector_store()
    return {
        "id": summary.id,
        "name": summary.name,
        "status": summary.status,
        "file_counts": summary.file_counts,
    }


@app.post("/runbooks/sync")
def sync_runbooks() -> dict[str, Any]:
    minio_client = _get_minio_client()
    return _sync_packaged_runbooks_to_minio(minio_client)


@app.post("/runbooks/ingest")
def ingest_runbooks() -> dict[str, Any]:
    minio_client = _get_minio_client()
    vector_client = _get_client()
    objects = minio_client.load_prefix_text_objects(settings.minio_runbook_prefix)
    ingested = []

    for obj in objects:
        summary: VectorStoreFileSummary = vector_client.ingest_text(
            filename=Path(obj.object_name).name,
            content=obj.content,
            attributes={"source_type": "runbook", "source_name": obj.object_name},
        )
        ingested.append(
            {
                "id": summary.id,
                "vector_store_id": summary.vector_store_id,
                "status": summary.status,
                "attributes": summary.attributes,
            }
        )

    return {
        "bucket": settings.minio_bucket,
        "prefix": settings.minio_runbook_prefix,
        "ingested_count": len(ingested),
        "objects": ingested,
    }


@app.get("/vector-store/files/{file_id}/content")
def vector_store_file_content(file_id: str) -> dict[str, Any]:
    client = _get_client()
    summary: VectorStoreFileContentSummary = client.get_file_content(file_id=file_id)
    return {
        "id": summary.id,
        "vector_store_id": summary.vector_store_id,
        "status": summary.status,
        "data": [
            {
                "text": item.text,
                "metadata": item.metadata,
                "embedding": item.embedding,
            }
            for item in summary.data
        ],
    }


def _get_ragas_client() -> RagasEvaluationClient:
    return RagasEvaluationClient(base_url=settings.llamastack_base_url)


def _resolve_model_id(client: Any, short_name: str) -> str:
    """Resolve a short model name to the full Llama Stack model ID.

    Llama Stack registers models as '{provider_id}/{model_name}'. This finds
    the full ID matching the given short name, falling back to the original
    name if no match is found.
    """
    if "/" in short_name:
        return short_name
    response = client.models.list()
    models = response.data if hasattr(response, "data") else list(response)
    for m in models:
        if m.id.endswith(f"/{short_name}"):
            return m.id
    return short_name


def _build_ragas_row(
    question: str,
    reference: str,
    retrieved_contexts: list[str],
    response: str,
) -> dict[str, Any]:
    return {
        "user_input": question,
        "response": response,
        "retrieved_contexts": retrieved_contexts,
        "reference": reference,
    }


@app.post("/evaluate")
def evaluate(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run RAGAS evaluation against the deployed RAG pipeline.

    Accepts body:
        scoring_functions: list of RAGAS metrics (default: ["answer_relevancy"])
        data: list of evaluation rows. Supports two formats:
              - RAGAS format: {user_input, response, retrieved_contexts, reference}
              - Project test-data format: {question, correct_answers}
                (contexts are retrieved from the vector store, responses
                 are generated by the LLM)
    """
    if not settings.ragas_inference_model:
        raise HTTPException(
            status_code=400,
            detail="RAGAS_INFERENCE_MODEL is not configured",
        )

    body = body or {}
    scoring_functions = body.get("scoring_functions", ["answer_relevancy"])
    raw_data = body.get("data")

    if not raw_data:
        raise HTTPException(status_code=400, detail="'data' field is required")

    from ogx_client import OgxClient

    llm_client = OgxClient(base_url=settings.llamastack_base_url, timeout=60)
    model_id = _resolve_model_id(llm_client, settings.ragas_inference_model)

    if "user_input" in raw_data[0]:
        evaluation_data = raw_data
    else:
        evaluation_data = _build_ragas_rows_from_test_cases(raw_data, llm_client, model_id)

    ragas_client = _get_ragas_client()
    result = ragas_client.evaluate(
        evaluation_data=evaluation_data,
        scoring_functions=scoring_functions,
        model_id=model_id,
    )

    return {
        "scores": {
            metric: {
                "aggregated": scores.aggregated,
                "per_row": scores.per_row,
            }
            for metric, scores in result.scores.items()
        },
        "row_count": len(result.generations),
    }


def _build_ragas_rows_from_test_cases(
    cases: list[dict[str, Any]],
    llm_client: Any,
    model_id: str,
) -> list[dict[str, Any]]:
    """Convert project test-data cases to RAGAS format.

    For each case: searches the vector store for contexts, calls the LLM for
    a response, and assembles a RAGAS evaluation row.
    """
    vector_client = _get_client()
    store = vector_client.get_vector_store()
    if store is None:
        raise HTTPException(
            status_code=400,
            detail=f"Vector store '{settings.vector_store_name}' not found; run ingestion first",
        )

    rows: list[dict[str, Any]] = []
    for case in cases:
        question = case["question"]
        reference = (case.get("correct_answers") or [""])[0]

        search_resp = llm_client.vector_stores.search(
            store.id, query=question, max_num_results=3
        )
        search_data = search_resp.data if hasattr(search_resp, "data") else list(search_resp)
        contexts = [
            hit.content[0].text
            for hit in search_data
            if hasattr(hit, "content") and hit.content
        ]
        if not contexts:
            contexts = [str(hit) for hit in search_data[:3]] if search_data else [""]

        context_block = "\n\n".join(contexts)
        chat_resp = llm_client.chat.completions.create(
            model=model_id,
            messages=[
                {
                    "role": "system",
                    "content": "Answer the question using only the provided context. Be concise.",
                },
                {
                    "role": "user",
                    "content": f"Context:\n{context_block}\n\nQuestion: {question}",
                },
            ],
            max_tokens=512,
        )
        response_text = chat_resp.choices[0].message.content or ""

        rows.append(_build_ragas_row(question, reference, contexts, response_text))
        logger.info(f"Built evaluation row for: {question[:60]}...")

    return rows
