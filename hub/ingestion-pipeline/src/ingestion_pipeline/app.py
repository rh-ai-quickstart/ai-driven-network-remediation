import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from ingestion_pipeline.clients.llamastack import (
    LlamaStackVectorStoreClient,
    VectorStoreFileContentSummary,
    VectorStoreSummary,
)
from ingestion_pipeline.config import settings
from ingestion_pipeline.service import IngestionPipelineService

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

_AUTO_INGEST = os.environ.get("AUTO_INGEST_ON_STARTUP", "true").lower() == "true"

service = IngestionPipelineService()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    if _AUTO_INGEST:
        import threading

        def _run_ingest():
            try:
                _auto_ingest()
            except Exception:
                logger.exception("Auto-ingest failed — retry via POST /runbooks/ingest")

        thread = threading.Thread(target=_run_ingest, daemon=True, name="auto-ingest")
        thread.start()
        logger.info("Auto-ingest started in background thread")
    yield


app = FastAPI(
    title="Ingestion Pipeline",
    description="Syncs packaged runbooks to MinIO and ingests them into a Llama Stack vector store",
    version="0.1.0",
    lifespan=lifespan,
)


def _auto_ingest() -> None:
    """Sync packaged runbooks to MinIO and ingest into the vector store.

    Checked here (rather than inside the service) so a missing MinIO/vector store just logs a
    warning and skips at startup, instead of failing deep inside sync()/ingest().
    """
    if not settings.minio_is_configured:
        logger.warning("MinIO not configured — skipping auto-ingest")
        return
    if not settings.vector_store_name:
        logger.warning("VECTOR_STORE_NAME not set — skipping auto-ingest")
        return

    sync_result = service.sync()
    logger.info(
        "Runbook sync complete: uploaded=%d skipped=%d",
        sync_result["uploaded_count"],
        sync_result["skipped_count"],
    )

    ingested = service.ingest()
    logger.info("Auto-ingest complete: %d runbooks ingested into '%s'", len(ingested), settings.vector_store_name)


def _get_client() -> LlamaStackVectorStoreClient:
    return LlamaStackVectorStoreClient(
        base_url=settings.llamastack_base_url,
        vector_store_name=settings.vector_store_name,
        embedding_model=settings.embedding_model,
        chunk_size_tokens=settings.chunk_size_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
    )


def _get_service() -> IngestionPipelineService:
    if not settings.minio_is_configured:
        raise HTTPException(status_code=400, detail="MinIO is not fully configured")
    return service


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
    service = _get_service()
    return service.sync()


@app.post("/runbooks/ingest")
def ingest_runbooks() -> dict[str, Any]:
    service = _get_service()
    ingested = service.ingest()

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
