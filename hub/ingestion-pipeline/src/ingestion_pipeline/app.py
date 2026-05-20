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
from ingestion_pipeline.config import settings

app = FastAPI(
    title="Ingestion Pipeline",
    description="Syncs packaged runbooks to MinIO and ingests them into a Llama Stack vector store",
    version="0.1.0",
)


def _get_client() -> LlamaStackVectorStoreClient:
    return LlamaStackVectorStoreClient(
        base_url=settings.llamastack_base_url,
        vector_store_name=settings.vector_store_name,
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
