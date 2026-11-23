"""Domain service for syncing and ingesting packaged runbooks and telco vendor docs.

This is the single implementation of "sync" and "ingest" for both corpora — the REST endpoints
and the startup auto-ingest hook in app.py call into this same service, rather than each having
their own copy of the logic. app.py stays a thin HTTP adapter: it only translates requests and
responses. All domain logic (MinIO uploads, document conversion, vector-store ingestion) lives
here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock
from typing import Any

from ingestion_pipeline.clients.llamastack import (
    IngestBatchResult,
    IngestItem,
    LlamaStackVectorStoreClient,
    VectorStoreFileContentSummary,
    VectorStoreSummary,
)
from ingestion_pipeline.clients.minio import MinioDocumentClient
from ingestion_pipeline.config import settings
from ingestion_pipeline.documents import (
    convert_to_markdown,
    markdown_object_name,
    original_filename_from_markdown_object,
    split_markdown_units,
    supported_extensions,
)

logger = logging.getLogger(__name__)


class IngestionAlreadyInProgressError(Exception):
    def __init__(self, corpus: str) -> None:
        super().__init__(f"{corpus} ingestion is already in progress")


class EmptyRunbookCorpusError(ValueError):
    """There are no source runbooks to replace the current corpus."""


class EmptyTelcoCorpusError(ValueError):
    """No document units were prepared to replace the current corpus."""


class IngestionBatchFailedError(Exception):
    """Some replacement attachments failed after clearing the previous corpus."""

    def __init__(self, result: dict[str, Any], *, rollback_errors: list[str]) -> None:
        self.result = {**result, "rollback_errors": rollback_errors} if rollback_errors else result
        super().__init__(f"{result['failed_count']} attachment(s) failed")


def _ingestion_result(batch: IngestBatchResult, *, prefix: str, store_name: str) -> dict[str, Any]:
    ingested = [
        {
            "id": summary.id,
            "vector_store_id": summary.vector_store_id,
            "status": summary.status,
            "attributes": summary.attributes,
        }
        for summary in batch.succeeded
    ]
    failed = [{"object_name": failure.item.filename, "reason": failure.reason} for failure in batch.failed]
    result = {
        "bucket": settings.minio_bucket,
        "prefix": prefix,
        "ingested_count": len(ingested),
        "failed_count": len(failed),
        "objects": ingested,
        "failed": failed,
    }
    logger.info("Ingest complete for '%s': ingested=%d failed=%d", store_name, len(ingested), len(failed))
    if failed:
        logger.warning("Ingest failures for '%s': %s", store_name, failed)
        raise IngestionBatchFailedError(result, rollback_errors=batch.rollback_errors)
    return result


def _runbook_object_name(filename: str) -> str:
    prefix = settings.minio_runbook_prefix.strip("/")
    if not prefix:
        return filename
    return f"{prefix}/{filename}"


def _telco_doc_object_name(filename: str) -> str:
    prefix = settings.minio_telco_docs_prefix.strip("/")
    if not prefix:
        return filename
    return f"{prefix}/{filename}"


class IngestionPipelineService:
    """Owns the MinIO and Llama Stack clients needed to sync and ingest runbooks and telco docs."""

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
            attachment_wait_timeout_seconds=settings.attachment_wait_timeout_seconds,
        )
        self._telco_vector_client = LlamaStackVectorStoreClient(
            base_url=settings.llamastack_base_url,
            vector_store_name=settings.telco_vector_store_name,
            embedding_model=settings.embedding_model,
            chunk_size_tokens=settings.chunk_size_tokens,
            chunk_overlap_tokens=settings.chunk_overlap_tokens,
            attachment_wait_timeout_seconds=settings.attachment_wait_timeout_seconds,
        )
        self._ingest_lock = Lock()
        self._telco_ingest_lock = Lock()

    # -- runbooks --

    def sync(self) -> dict[str, Any]:
        """Upload any packaged runbooks that aren't already in MinIO."""
        self._minio_client.ensure_bucket()
        uploaded: list[str] = []
        skipped: list[str] = []
        failed: list[dict[str, str]] = []
        if settings.runbooks_dir.exists():
            for runbook_path in sorted(settings.runbooks_dir.glob("*.md")):
                object_name = _runbook_object_name(runbook_path.name)
                try:
                    was_uploaded = self._minio_client.put_text_object_if_missing(
                        object_name,
                        runbook_path.read_text(encoding="utf-8"),
                    )
                    if was_uploaded:
                        uploaded.append(object_name)
                    else:
                        skipped.append(object_name)
                except Exception as exc:
                    logger.exception("Failed to sync '%s'", runbook_path.name)
                    failed.append({"object_name": object_name, "reason": str(exc)})

        result = {
            "bucket": settings.minio_bucket,
            "prefix": settings.minio_runbook_prefix,
            "uploaded_count": len(uploaded),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
            "uploaded_objects": uploaded,
            "skipped_objects": skipped,
            "failed_objects": failed,
        }
        logger.info(
            "Runbook sync complete: uploaded=%d skipped=%d failed=%d",
            len(uploaded),
            len(skipped),
            len(failed),
        )
        if failed:
            logger.warning("Runbook sync failures: %s", failed)
        return result

    def ingest(self) -> dict[str, Any]:
        """Replace the runbook attachments in the existing vector store."""
        if not self._ingest_lock.acquire(blocking=False):
            raise IngestionAlreadyInProgressError("Runbook")
        try:
            objects = self._minio_client.load_prefix_text_objects(settings.minio_runbook_prefix)
            items = [
                IngestItem(
                    filename=Path(obj.object_name).name,
                    content=obj.content,
                    attributes={"source_type": "runbook", "source_name": obj.object_name},
                )
                for obj in objects
            ]
            if not items:
                raise EmptyRunbookCorpusError("No runbooks found for ingestion; existing attachments were preserved")

            vector_store = self._vector_client.ensure_vector_store()
            batch_result = self._vector_client.replace_text_batch(
                vector_store_id=vector_store.id,
                items=items,
            )
            return _ingestion_result(
                batch_result, prefix=settings.minio_runbook_prefix, store_name=settings.vector_store_name
            )
        finally:
            self._ingest_lock.release()

    # -- telco vendor docs --

    def sync_telco_docs(self) -> dict[str, Any]:
        """Convert every packaged vendor doc to markdown and write it to MinIO.

        Conversion is deterministic (same source bytes + converter code always produce the same
        markdown), so objects are always overwritten rather than skipped-if-present — a converter
        fix or improvement takes effect automatically on the next deploy.
        """
        self._minio_client.ensure_bucket()
        self._minio_client.delete_prefix_objects(settings.minio_telco_docs_prefix)
        converted: list[str] = []
        failed: list[dict[str, str]] = []
        scan_dirs = [settings.telco_docs_dir / "mandatory"]
        if settings.telco_docs_include_optional:
            scan_dirs.append(settings.telco_docs_dir / "optional")
        for scan_dir in scan_dirs:
            if not scan_dir.exists():
                continue
            for extension in sorted(supported_extensions()):
                for doc_path in sorted(scan_dir.glob(f"*{extension}")):
                    object_name = _telco_doc_object_name(markdown_object_name(doc_path.name))
                    try:
                        markdown_text = convert_to_markdown(doc_path.name, doc_path.read_bytes())
                        self._minio_client.put_text_object(object_name, markdown_text)
                        converted.append(object_name)
                    except Exception as exc:
                        logger.exception("Failed to convert/sync '%s'", doc_path.name)
                        failed.append({"object_name": object_name, "reason": str(exc)})

        result = {
            "bucket": settings.minio_bucket,
            "prefix": settings.minio_telco_docs_prefix,
            "converted_count": len(converted),
            "failed_count": len(failed),
            "converted_objects": converted,
            "failed_objects": failed,
        }
        logger.info(
            "Telco doc sync complete: converted=%d failed=%d",
            len(converted),
            len(failed),
        )
        if failed:
            logger.warning("Telco doc sync failures: %s", failed)
        return result

    def ingest_telco_docs(self) -> dict[str, Any]:
        """Replace vendor-document units in the existing telco vector store."""
        if not self._telco_ingest_lock.acquire(blocking=False):
            raise IngestionAlreadyInProgressError("Telco")
        try:
            objects = self._minio_client.load_prefix_text_objects(settings.minio_telco_docs_prefix)

            items: list[IngestItem] = []
            for obj in objects:
                source_name = original_filename_from_markdown_object(obj.object_name)
                for index, unit in enumerate(split_markdown_units(obj.content), start=1):
                    items.append(
                        IngestItem(
                            filename=f"{Path(source_name).stem}#{index:03d}.md",
                            content=unit.text,
                            attributes={
                                "source_type": "vendor_doc",
                                "source_name": source_name,
                                **unit.attributes,
                            },
                        )
                    )

            if not items:
                raise EmptyTelcoCorpusError(
                    "No Telco document units found for ingestion; existing attachments were preserved"
                )

            vector_store = self._telco_vector_client.ensure_vector_store()
            batch_result = self._telco_vector_client.replace_text_batch(
                vector_store_id=vector_store.id,
                items=items,
            )
            return _ingestion_result(
                batch_result, prefix=settings.minio_telco_docs_prefix, store_name=settings.telco_vector_store_name
            )
        finally:
            self._telco_ingest_lock.release()

    # -- read-only inspection (backed by the runbook vector store) --

    def list_models(self) -> list[dict[str, Any]]:
        return self._vector_client.list_models()

    def vector_store_summary(self) -> VectorStoreSummary:
        return self._vector_client.ensure_vector_store()

    def vector_store_file_content(self, file_id: str, vector_store_id: str) -> VectorStoreFileContentSummary:
        # Both vector clients talk to the same Llama Stack instance, so either can serve any
        # vector_store_id — the caller (who already has it from a prior ingest response) picks
        # which store's file to fetch, rather than us guessing based on our own config.
        return self._vector_client.get_file_content(file_id=file_id, vector_store_id=vector_store_id)
