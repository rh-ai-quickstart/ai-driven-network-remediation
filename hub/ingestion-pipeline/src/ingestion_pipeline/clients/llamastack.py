from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ogx_client import OgxClient
from ogx_client.types.vector_stores import VectorStoreFile
from ogx_client.types.vector_stores.file_create_params import ChunkingStrategy

logger = logging.getLogger(__name__)


class DuplicateVectorStoreNameError(ValueError):
    """The configured name cannot identify a single vector store."""


class AttachmentListError(RuntimeError):
    """The provider could not enumerate a store's attachments."""


class AttachmentDetachError(RuntimeError):
    """The provider could not remove a store's attachments."""


@dataclass(frozen=True)
class VectorStoreSummary:
    id: str
    name: str | None
    status: str | None
    file_counts: dict[str, int]


@dataclass(frozen=True)
class IngestItem:
    filename: str
    content: str
    attributes: dict[str, str | float | bool] | None = None


@dataclass(frozen=True)
class FailedIngestItem:
    item: IngestItem
    reason: str


@dataclass(frozen=True)
class IngestBatchResult:
    succeeded: list[VectorStoreFileSummary]
    failed: list[FailedIngestItem]


@dataclass(frozen=True)
class VectorStoreFileSummary:
    id: str
    vector_store_id: str
    status: str
    attributes: dict[str, str | float | bool] | None


@dataclass(frozen=True)
class VectorStoreFileContentItem:
    text: str
    metadata: dict[str, object] | None
    embedding: list[float] | None


@dataclass(frozen=True)
class VectorStoreFileContentSummary:
    id: str
    vector_store_id: str
    status: str
    data: list[VectorStoreFileContentItem]


class LlamaStackVectorStoreClient:
    def __init__(
        self,
        *,
        base_url: str,
        vector_store_name: str,
        embedding_model: str | None = None,
        chunk_size_tokens: int = 800,
        chunk_overlap_tokens: int = 80,
        # The first file attachment can download and initialize the embedding model.
        timeout_seconds: float = 300,
    ) -> None:
        self._client = OgxClient(base_url=base_url, timeout=timeout_seconds)
        self._vector_store_name = vector_store_name
        self._embedding_model = embedding_model
        self._chunk_size_tokens = chunk_size_tokens
        self._chunk_overlap_tokens = chunk_overlap_tokens

    def list_models(self) -> list[dict[str, Any]]:
        response = self._client.models.list()
        models = response.data if hasattr(response, "data") else list(response)
        return [model.model_dump() for model in models]

    def get_vector_store(self) -> VectorStoreSummary | None:
        vector_store = self._find_vector_store_by_name(self._vector_store_name)
        if vector_store is None:
            return None

        return VectorStoreSummary(
            id=vector_store.id,
            name=vector_store.name,
            status=vector_store.status,
            file_counts=vector_store.file_counts.model_dump(),
        )

    def delete_vector_store(self) -> bool:
        existing = self._find_vector_store_by_name(self._vector_store_name)
        if existing is None:
            return False
        self._client.vector_stores.delete(existing.id)
        return True

    def ensure_vector_store(self) -> VectorStoreSummary:
        existing = self.get_vector_store()
        if existing is not None:
            return existing

        create_kwargs: dict[str, Any] = {"name": self._vector_store_name}
        if self._embedding_model:
            create_kwargs["extra_body"] = {"embedding_model": self._embedding_model}
        created = self._client.vector_stores.create(**create_kwargs)
        return VectorStoreSummary(
            id=created.id,
            name=created.name,
            status=created.status,
            file_counts=created.file_counts.model_dump(),
        )

    def _detach_file(self, vector_store_id: str, file_id: str) -> None:
        message = f"Failed to detach file '{file_id}' from vector store '{vector_store_id}'"
        try:
            response = self._client.vector_stores.files.delete(file_id, vector_store_id=vector_store_id)
        except Exception as exc:
            raise AttachmentDetachError(f"{message}: {exc}") from exc
        if not response.deleted:
            raise AttachmentDetachError(message)

    def _list_attachment_ids(self, vector_store_id: str) -> list[str]:
        try:
            return [
                file.id for file in self._iter_pages(self._client.vector_stores.files.list(vector_store_id, limit=100))
            ]
        except Exception as exc:
            raise AttachmentListError(f"Could not list attachments in vector store '{vector_store_id}': {exc}") from exc

    def _rollback_attachments(self, vector_store_id: str, attempted_ids: list[str]) -> None:
        if not attempted_ids:
            return

        for file_id in attempted_ids:
            try:
                self._detach_file(vector_store_id, file_id)
            except AttachmentDetachError as exc:
                # Llama Stack can index chunks before persisting attachment metadata.
                logger.warning("Rollback could not confirm detach: %s", exc)

        try:
            remaining_ids = set(self._list_attachment_ids(vector_store_id)).intersection(attempted_ids)
        except AttachmentListError as exc:
            logger.error(
                "Rollback in vector store '%s' could not verify removal of %s: %s",
                vector_store_id,
                attempted_ids,
                exc,
            )
            return
        if remaining_ids:
            logger.error("Rollback in vector store '%s' could not remove %s", vector_store_id, sorted(remaining_ids))

    def replace_text_batch(
        self,
        *,
        vector_store_id: str,
        items: list[IngestItem],
    ) -> IngestBatchResult:
        """Attach a replacement corpus before removing the previous attachments."""
        if not items:
            raise ValueError("A replacement corpus must contain at least one item")

        original_ids = self._list_attachment_ids(vector_store_id)
        chunking_strategy: ChunkingStrategy = {
            "type": "static",
            "static": {
                "max_chunk_size_tokens": self._chunk_size_tokens,
                "chunk_overlap_tokens": self._chunk_overlap_tokens,
            },
        }

        logger.info("Ingesting %d files into vector store %s", len(items), vector_store_id)
        attempted_ids: list[str] = []
        succeeded: list[VectorStoreFileSummary] = []
        failed: list[FailedIngestItem] = []
        for item in items:
            try:
                created_file = self._client.files.create(
                    file=(item.filename, item.content.encode("utf-8"), "text/markdown"),
                    purpose="assistants",
                )
                # The provider may attach the file even if the create response is lost.
                attempted_ids.append(created_file.id)
                attached_file = self._client.vector_stores.files.create(
                    vector_store_id,
                    file_id=created_file.id,
                    attributes=item.attributes,
                    chunking_strategy=chunking_strategy,
                )
                if attached_file.status == "in_progress":
                    attached_file = self._wait_for_file_ready(file_id=attached_file.id, vector_store_id=vector_store_id)
                if attached_file.status != "completed":
                    last_error = getattr(attached_file, "last_error", None)
                    reason = getattr(last_error, "message", None) or f"Attachment status: {attached_file.status}"
                    failed.append(FailedIngestItem(item=item, reason=reason))
                    continue
                succeeded.append(
                    VectorStoreFileSummary(
                        id=attached_file.id,
                        vector_store_id=attached_file.vector_store_id,
                        status=attached_file.status,
                        attributes=attached_file.attributes,
                    )
                )
            except Exception as exc:
                logger.exception("Failed to ingest '%s'", item.filename)
                failed.append(FailedIngestItem(item=item, reason=str(exc)))

        if failed:
            self._rollback_attachments(vector_store_id, attempted_ids)
        elif original_ids:
            for file_id in original_ids:
                self._detach_file(vector_store_id, file_id)
            remaining_ids = set(self._list_attachment_ids(vector_store_id))
            if originals_remaining := remaining_ids.intersection(original_ids):
                raise AttachmentDetachError(
                    f"Previous attachments remain in vector store '{vector_store_id}': {sorted(originals_remaining)}"
                )
        return IngestBatchResult(succeeded=succeeded, failed=failed)

    def get_file_content(
        self,
        *,
        file_id: str,
        vector_store_id: str,
        wait_timeout_seconds: float = 300,
        poll_interval_seconds: float = 1,
    ) -> VectorStoreFileContentSummary:
        vector_file = self._wait_for_file_ready(
            file_id=file_id,
            vector_store_id=vector_store_id,
            wait_timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        content = self._client.vector_stores.files.content(
            file_id,
            vector_store_id=vector_store_id,
            include_embeddings=True,
            include_metadata=True,
        )
        return VectorStoreFileContentSummary(
            id=vector_file.id,
            vector_store_id=vector_file.vector_store_id,
            status=vector_file.status,
            data=[
                VectorStoreFileContentItem(
                    text=item.text,
                    metadata=item.metadata,
                    embedding=item.embedding,
                )
                for item in content.data
            ],
        )

    def _find_vector_store_by_name(self, name: str) -> Any | None:
        matches = [
            store for store in self._iter_pages(self._client.vector_stores.list(limit=100)) if store.name == name
        ]
        if len(matches) > 1:
            raise DuplicateVectorStoreNameError(f"Multiple vector stores named '{name}'")
        return matches[0] if matches else None

    @classmethod
    def _iter_pages(cls, page: Any) -> Iterable[Any]:
        while True:
            yield from cls._iter_items(page)
            if not page.has_next_page():
                break
            page = page.get_next_page()

    def _wait_for_file_ready(
        self,
        *,
        file_id: str,
        vector_store_id: str,
        wait_timeout_seconds: float = 300,
        poll_interval_seconds: float = 1,
    ) -> VectorStoreFile:
        deadline = time.monotonic() + wait_timeout_seconds
        while True:
            vector_file = self._client.vector_stores.files.retrieve(file_id, vector_store_id=vector_store_id)
            if vector_file.status != "in_progress":
                return vector_file
            if time.monotonic() >= deadline:
                return vector_file
            time.sleep(poll_interval_seconds)

    @staticmethod
    def _iter_items(result: Any) -> Iterable[Any]:
        if hasattr(result, "data"):
            return result.data
        return list(result)
