"""Reusable async RAG client backed by LlamaStack vector stores."""

from __future__ import annotations

from llama_stack_client import APIStatusError, AsyncLlamaStackClient, BadRequestError
from llama_stack_client.types import VectorStoreSearchParams
from llama_stack_client.types.vector_stores import FileCreateParams
from loguru import logger


def _store_id_not_found(error: BadRequestError, store_id: str) -> bool:
    body = error.body
    if not isinstance(body, dict):
        return False
    detail = body.get("error")
    return isinstance(detail, dict) and detail.get("message") == f"Vector_store '{store_id}' not found"


class RagClient:
    """Query a LlamaStack vector store by name, with lazy resolution."""

    def __init__(self, *, host: str, port: int, vector_store_name: str) -> None:
        self._client = AsyncLlamaStackClient(base_url=f"http://{host}:{port}")
        self._vector_store_name = vector_store_name
        self._search_store_id: str | None = None
        self._upload_store_id: str | None = None

    async def search(
        self,
        query: str,
        *,
        max_num_results: int = 5,
        score_threshold: float = 0.3,
    ) -> list[str]:
        vs_id = await self._resolve_search_store_id()
        if vs_id is None:
            return []

        search_options: VectorStoreSearchParams = {
            "query": query,
            "max_num_results": max_num_results,
            "ranking_options": {"score_threshold": score_threshold},
        }
        search_store = self._client.with_options(max_retries=0).vector_stores.search
        try:
            response = await search_store(vs_id, **search_options)
        except APIStatusError:
            try:
                resolved_id = await self._resolve_search_store_id(failed_id=vs_id)
            except Exception:
                # If the lookup fails, preserve the original search error.
                resolved_id = vs_id
            if resolved_id == vs_id:
                raise
            if resolved_id is None:
                return []
            response = await search_store(resolved_id, **search_options)
        return list(dict.fromkeys(content.text for item in response.data for content in item.content))

    async def upload_file(
        self,
        filename: str,
        content: bytes,
        *,
        mime_type: str = "text/markdown",
        chunk_size_tokens: int = 512,
        chunk_overlap_tokens: int = 64,
    ) -> None:
        cached_id = self._upload_store_id
        vs_id = await self._resolve_upload_store_id()
        if vs_id is None:
            return

        created_file = await self._client.files.create(
            file=(filename, content, mime_type),
            purpose="assistants",
        )
        attach_options: FileCreateParams = {
            "file_id": created_file.id,
            "chunking_strategy": {
                "type": "static",
                "static": {
                    "max_chunk_size_tokens": chunk_size_tokens,
                    "chunk_overlap_tokens": chunk_overlap_tokens,
                },
            },
        }
        try:
            await self._client.vector_stores.files.create(vs_id, **attach_options)
        except BadRequestError as error:
            if cached_id != vs_id or not _store_id_not_found(error, vs_id):
                raise
            self._upload_store_id = None
            vs_id = await self._resolve_upload_store_id()
            if vs_id is None:
                return
            await self._client.vector_stores.files.create(vs_id, **attach_options)

    async def _resolve_search_store_id(self, *, failed_id: str | None = None) -> str | None:
        if self._search_store_id != failed_id:
            return self._search_store_id

        match_ids = await self._list_matching_store_ids()
        # Keep the selection if another search resolved the name while listing stores.
        if self._search_store_id != failed_id:
            return self._search_store_id

        selected_id = failed_id if failed_id in match_ids else min(match_ids, default=None)
        if len(match_ids) > 1:
            logger.warning(
                "Multiple vector stores named '{}': {}; searching '{}'",
                self._vector_store_name,
                sorted(match_ids),
                selected_id,
            )
        self._search_store_id = selected_id
        return selected_id

    async def _resolve_upload_store_id(self) -> str | None:
        if self._upload_store_id is not None:
            return self._upload_store_id

        match_ids = await self._list_matching_store_ids()
        if len(match_ids) > 1:
            raise ValueError(f"Multiple vector stores named '{self._vector_store_name}'")
        self._upload_store_id = next(iter(match_ids), None)
        return self._upload_store_id

    async def _list_matching_store_ids(self) -> set[str]:
        page = await self._client.vector_stores.list(limit=100)
        match_ids: set[str] = set()
        while True:
            match_ids.update(store.id for store in page.data if store.name == self._vector_store_name)
            if not page.has_next_page():
                break
            page = await page.get_next_page()

        if not match_ids:
            logger.warning("Vector store '{}' not found", self._vector_store_name)
        return match_ids
