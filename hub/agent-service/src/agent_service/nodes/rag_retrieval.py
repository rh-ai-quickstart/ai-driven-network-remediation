import os

from llama_stack_client import AsyncLlamaStackClient
from loguru import logger

_LLAMASTACK_HOST = os.environ.get("LLAMASTACK_HOST", "localhost")
_LLAMASTACK_PORT = os.environ.get("LLAMASTACK_PORT", "8321")
_VECTOR_STORE_NAME = os.environ.get("VECTOR_STORE_NAME", "noc_runbooks")

_client = AsyncLlamaStackClient(base_url=f"http://{_LLAMASTACK_HOST}:{_LLAMASTACK_PORT}")
_vector_store_id: str | None = None


async def _resolve_vector_store_id() -> str | None:
    global _vector_store_id
    if _vector_store_id is not None:
        return _vector_store_id

    result = await _client.vector_stores.list(limit=100)
    for vs in result.data:
        if vs.name == _VECTOR_STORE_NAME:
            _vector_store_id = vs.id
            return _vector_store_id
    logger.warning(f"Vector store '{_VECTOR_STORE_NAME}' not found")
    return None


async def rag_retrieval_node(state: dict) -> dict:
    logger.info("RAG retrieval node invoked")
    log_event = state.log_event
    query = f"{log_event.message} namespace={log_event.namespace} pod={log_event.pod_name}"

    try:
        vs_id = await _resolve_vector_store_id()
        if vs_id is None:
            return {"context_snippets": [], "rag_query_used": query}

        response = await _client.vector_stores.search(
            vs_id,
            query=query,
            max_num_results=5,
            ranking_options={"score_threshold": 0.3},
        )
        snippets = [content.text for item in response.data for content in item.content]
        return {"context_snippets": snippets, "rag_query_used": query}
    except Exception:
        logger.exception("LlamaStack search failed")
        return {"context_snippets": [], "rag_query_used": query}
