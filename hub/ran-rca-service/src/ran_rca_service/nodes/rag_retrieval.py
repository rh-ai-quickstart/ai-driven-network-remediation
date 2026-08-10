"""RAG retrieval node — queries the telco_oran_docs vector store via LlamaStack."""

from __future__ import annotations

import time

from llama_stack_client import AsyncLlamaStackClient
from loguru import logger

from ran_rca_service.config import LLAMASTACK_URL, VECTOR_STORE_NAME
from ran_rca_service.models import RCAState

_client = AsyncLlamaStackClient(base_url=LLAMASTACK_URL)
_vector_store_id: str | None = None
_negative_cache_until: float = 0.0

_NEGATIVE_CACHE_TTL_SECONDS = 300.0


async def _resolve_vector_store_id() -> str | None:
    global _vector_store_id, _negative_cache_until
    if _vector_store_id is not None:
        return _vector_store_id

    if time.monotonic() < _negative_cache_until:
        return None

    result = await _client.vector_stores.list(limit=100)
    for vs in result.data:
        if vs.name == VECTOR_STORE_NAME:
            _vector_store_id = vs.id
            return _vector_store_id
    logger.warning(f"Vector store '{VECTOR_STORE_NAME}' not found, retrying in {_NEGATIVE_CACHE_TTL_SECONDS}s")
    _negative_cache_until = time.monotonic() + _NEGATIVE_CACHE_TTL_SECONDS
    return None


def _result(query: str, snippets: list[str] | None = None) -> dict:
    return {"context_snippets": snippets or [], "rag_query_used": query}


async def rag_retrieval_node(state: RCAState) -> dict:
    query = f"{state.anomaly_type} {state.anomaly} {state.band}"

    try:
        vs_id = await _resolve_vector_store_id()
        if vs_id is None:
            return _result(query)

        response = await _client.vector_stores.search(
            vs_id,
            query=query,
            max_num_results=5,
            ranking_options={"score_threshold": 0.3},
        )
        snippets = [content.text for item in response.data for content in item.content]
        return _result(query, snippets)
    except Exception:
        logger.exception("LlamaStack search failed")
        return _result(query)
