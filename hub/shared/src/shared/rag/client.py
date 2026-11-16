"""Reusable async RAG client backed by LlamaStack vector stores."""

from __future__ import annotations

from llama_stack_client import AsyncLlamaStackClient
from loguru import logger


class RagClient:
    """Query a LlamaStack vector store by name, with lazy resolution."""

    def __init__(self, *, host: str, port: int, vector_store_name: str) -> None:
        self._client = AsyncLlamaStackClient(base_url=f"http://{host}:{port}")
        self._vector_store_name = vector_store_name
        self._vector_store_id: str | None = None

    async def search(
        self,
        query: str,
        *,
        max_num_results: int = 5,
        score_threshold: float = 0.3,
    ) -> list[str]:
        vs_id = await self._resolve_vector_store_id()
        if vs_id is None:
            return []

        response = await self._client.vector_stores.search(
            vs_id,
            query=query,
            max_num_results=max_num_results,
            ranking_options={"score_threshold": score_threshold},
        )
        return [content.text for item in response.data for content in item.content]

    async def upload_file(
        self,
        filename: str,
        content: bytes,
        *,
        mime_type: str = "text/markdown",
        chunk_size_tokens: int = 512,
        chunk_overlap_tokens: int = 64,
    ) -> None:
        vs_id = await self._resolve_vector_store_id()
        if vs_id is None:
            return

        created_file = await self._client.files.create(
            file=(filename, content, mime_type),
            purpose="assistants",
        )
        await self._client.vector_stores.files.create(
            vs_id,
            file_id=created_file.id,
            chunking_strategy={
                "type": "static",
                "static": {
                    "max_chunk_size_tokens": chunk_size_tokens,
                    "chunk_overlap_tokens": chunk_overlap_tokens,
                },
            },
        )

    async def _resolve_vector_store_id(self) -> str | None:
        if self._vector_store_id is not None:
            return self._vector_store_id

        page = await self._client.vector_stores.list(limit=100)
        match_id: str | None = None
        while True:
            for vs in page.data:
                if vs.name == self._vector_store_name:
                    if match_id is not None:
                        raise ValueError(f"Multiple vector stores named '{self._vector_store_name}'")
                    match_id = vs.id
            if not page.has_next_page():
                break
            page = await page.get_next_page()

        if match_id is not None:
            self._vector_store_id = match_id
            return match_id

        logger.warning("Vector store '{}' not found", self._vector_store_name)
        return None
