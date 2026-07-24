"""Domain service for syncing and ingesting packaged runbooks.

This is the single implementation of "sync" and "ingest" for runbooks — both the REST
endpoints in app.py and the startup auto-ingest hook call into this same service, rather than
each having their own copy of the logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ingestion_pipeline.clients.llamastack import LlamaStackVectorStoreClient, VectorStoreFileSummary
from ingestion_pipeline.clients.minio import MinioDocumentClient
from ingestion_pipeline.config import settings


def _runbook_object_name(filename: str) -> str:
    prefix = settings.minio_runbook_prefix.strip("/")
    if not prefix:
        return filename
    return f"{prefix}/{filename}"


class IngestionPipelineService:
    """Owns the MinIO and Llama Stack clients needed to sync and ingest runbooks."""

    def __init__(self) -> None:
        self._minio_client = MinioDocumentClient(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket,
            secure=settings.minio_secure,
        )
        self._vector_client = LlamaStackVectorStoreClient(
            base_url=settings.llamastack_base_url,
            vector_store_name=settings.vector_store_name,
            embedding_model=settings.embedding_model,
            chunk_size_tokens=settings.chunk_size_tokens,
            chunk_overlap_tokens=settings.chunk_overlap_tokens,
        )

    def sync(self) -> dict[str, Any]:
        """Upload any packaged runbooks that aren't already in MinIO."""
        self._minio_client.ensure_bucket()
        uploaded: list[str] = []
        skipped: list[str] = []
        if settings.runbooks_dir.exists():
            for runbook_path in sorted(settings.runbooks_dir.glob("*.md")):
                object_name = _runbook_object_name(runbook_path.name)
                was_uploaded = self._minio_client.put_text_object_if_missing(
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

    def ingest(self) -> list[dict[str, Any]]:
        """Ingest every synced runbook from MinIO into the vector store."""
        self._vector_client.ensure_vector_store()
        objects = self._minio_client.load_prefix_text_objects(settings.minio_runbook_prefix)
        ingested: list[dict[str, Any]] = []

        for obj in objects:
            summary: VectorStoreFileSummary = self._vector_client.ingest_text(
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

        return ingested
