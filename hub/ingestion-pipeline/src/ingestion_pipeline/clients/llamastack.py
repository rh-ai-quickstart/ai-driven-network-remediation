from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable

from ogx_client import OgxClient


@dataclass(frozen=True)
class VectorStoreSummary:
    id: str
    name: str | None
    status: str | None
    file_counts: dict[str, int]


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
        timeout_seconds: float = 30,
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

    def ingest_text(
        self,
        *,
        filename: str,
        content: str,
        attributes: dict[str, str | float | bool] | None = None,
        chunk_size_tokens: int | None = None,
        chunk_overlap_tokens: int | None = None,
    ) -> VectorStoreFileSummary:
        chunk_size_tokens = chunk_size_tokens if chunk_size_tokens is not None else self._chunk_size_tokens
        chunk_overlap_tokens = chunk_overlap_tokens if chunk_overlap_tokens is not None else self._chunk_overlap_tokens
        vector_store = self.ensure_vector_store()
        created_file = self._client.files.create(
            file=(filename, content.encode("utf-8"), "text/markdown"),
            purpose="assistants",
        )
        attached_file = self._client.vector_stores.files.create(
            vector_store.id,
            file_id=created_file.id,
            attributes=attributes,
            chunking_strategy={
                "type": "static",
                "static": {
                    "max_chunk_size_tokens": chunk_size_tokens,
                    "chunk_overlap_tokens": chunk_overlap_tokens,
                },
            },
        )
        return VectorStoreFileSummary(
            id=attached_file.id,
            vector_store_id=attached_file.vector_store_id,
            status=attached_file.status,
            attributes=attached_file.attributes,
        )

    def get_file_content(
        self,
        *,
        file_id: str,
        wait_timeout_seconds: float = 30,
        poll_interval_seconds: float = 1,
    ) -> VectorStoreFileContentSummary:
        vector_store = self.ensure_vector_store()
        vector_file = self._wait_for_file_ready(
            file_id=file_id,
            vector_store_id=vector_store.id,
            wait_timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        content = self._client.vector_stores.files.content(
            file_id,
            vector_store_id=vector_store.id,
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
        for vector_store in self._iter_items(self._client.vector_stores.list(limit=100)):
            if vector_store.name == name:
                return vector_store
        return None

    def _wait_for_file_ready(
        self,
        *,
        file_id: str,
        vector_store_id: str,
        wait_timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> Any:
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
