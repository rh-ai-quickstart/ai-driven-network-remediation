import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from ingestion_pipeline.clients.llamastack import VectorStoreFileContentSummary, VectorStoreSummary
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
                logger.exception("Auto-ingest failed — retry via POST /runbooks/ingest or /telco-docs/ingest")

        thread = threading.Thread(target=_run_ingest, daemon=True, name="auto-ingest")
        thread.start()
        logger.info("Auto-ingest started in background thread")
    yield


app = FastAPI(
    title="Ingestion Pipeline",
    description=(
        "Syncs packaged runbooks and RAN/ORAN vendor documentation to MinIO "
        "and ingests them into Llama Stack vector stores"
    ),
    version="0.1.0",
    lifespan=lifespan,
)


def _auto_ingest() -> None:
    """Sync packaged runbooks and vendor docs to MinIO and ingest into their vector stores."""
    if not settings.minio_is_configured:
        logger.warning("MinIO not configured — skipping auto-ingest")
        return
    if not settings.vector_store_name and not settings.telco_vector_store_name:
        logger.warning("No vector store configured — skipping auto-ingest")
        return

    if settings.vector_store_name:
        service.sync()
        service.ingest()
    else:
        logger.warning("VECTOR_STORE_NAME not set — skipping runbook auto-ingest")

    if settings.telco_vector_store_name:
        service.sync_telco_docs()
        service.ingest_telco_docs()
    else:
        logger.warning("TELCO_VECTOR_STORE_NAME not set — skipping telco doc auto-ingest")


def _get_service() -> IngestionPipelineService:
    if not settings.minio_is_configured:
        raise HTTPException(status_code=400, detail="MinIO is not fully configured")
    return service


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/models")
def models() -> dict[str, Any]:
    return {"models": service.list_models()}


@app.get("/vector-store")
def vector_store() -> dict[str, Any]:
    summary: VectorStoreSummary = service.vector_store_summary()
    return {
        "id": summary.id,
        "name": summary.name,
        "status": summary.status,
        "file_counts": summary.file_counts,
    }


@app.post("/runbooks/sync")
def sync_runbooks() -> dict[str, Any]:
    return _get_service().sync()


@app.post("/runbooks/ingest")
def ingest_runbooks() -> dict[str, Any]:
    return _get_service().ingest()


@app.post("/telco-docs/sync")
def sync_telco_docs() -> dict[str, Any]:
    return _get_service().sync_telco_docs()


@app.post("/telco-docs/ingest")
def ingest_telco_docs() -> dict[str, Any]:
    return _get_service().ingest_telco_docs()


@app.get("/vector-store/files/{file_id}/content")
def vector_store_file_content(file_id: str, vector_store_id: str) -> dict[str, Any]:
    summary: VectorStoreFileContentSummary = service.vector_store_file_content(file_id, vector_store_id)
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
