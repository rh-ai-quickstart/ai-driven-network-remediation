import time

from llama_stack_client import AsyncLlamaStackClient
from loguru import logger

from agent_service.config import (
    LLAMASTACK_HOST,
    LLAMASTACK_PORT,
    VECTOR_STORE_CHUNK_OVERLAP_TOKENS,
    VECTOR_STORE_CHUNK_SIZE_TOKENS,
    VECTOR_STORE_NAME,
)

_client = AsyncLlamaStackClient(base_url=f"http://{LLAMASTACK_HOST}:{LLAMASTACK_PORT}")
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


# TODO: validate that the playbook actually solved the problem before storing.
# Currently we assume all generated playbooks are good.
async def store_generated_playbook(
    playbook_name: str,
    playbook_yaml: str,
    failure_type: str,
    summary: str,
) -> None:
    # TODO: consider changing ALS prompt to return explanation alongside
    # playbook for richer vector store content.
    content = f"Failure: {failure_type}\nSummary: {summary}\nPlaybook ({failure_type}):\n{playbook_yaml}"
    filename = f"{playbook_name}.md"

    try:
        vs_id = await _resolve_vector_store_id()
        if vs_id is None:
            return

        created_file = await _client.files.create(
            file=(filename, content.encode("utf-8"), "text/markdown"),
            purpose="assistants",
        )
        await _client.vector_stores.files.create(
            vs_id,
            file_id=created_file.id,
            chunking_strategy={
                "type": "static",
                "static": {
                    "max_chunk_size_tokens": VECTOR_STORE_CHUNK_SIZE_TOKENS,
                    "chunk_overlap_tokens": VECTOR_STORE_CHUNK_OVERLAP_TOKENS,
                },
            },
        )
        logger.info(f"Stored generated playbook '{playbook_name}' in vector store")
    except Exception:
        logger.exception(f"Failed to store playbook '{playbook_name}' in vector store")
