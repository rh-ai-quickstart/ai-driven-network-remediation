"""RAG retrieval node — queries the noc_runbooks vector store via shared RagClient."""

from __future__ import annotations

from loguru import logger
from shared.rag import RagClient

from agent_service.config import (
    LLAMASTACK_HOST,
    LLAMASTACK_PORT,
    VECTOR_STORE_CHUNK_OVERLAP_TOKENS,
    VECTOR_STORE_CHUNK_SIZE_TOKENS,
    VECTOR_STORE_NAME,
)

_rag_client: RagClient | None = None


def _get_rag_client() -> RagClient:
    global _rag_client
    if _rag_client is None:
        _rag_client = RagClient(
            host=LLAMASTACK_HOST,
            port=int(LLAMASTACK_PORT),
            vector_store_name=VECTOR_STORE_NAME,
        )
    return _rag_client


async def rag_retrieval_node(state: dict) -> dict:
    logger.info("RAG retrieval node invoked")
    log_event = state.log_event
    query = f"{log_event.message} namespace={log_event.namespace} pod={log_event.pod_name}"

    try:
        snippets = await _get_rag_client().search(query)
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
        await _get_rag_client().upload_file(
            filename,
            content.encode("utf-8"),
            chunk_size_tokens=VECTOR_STORE_CHUNK_SIZE_TOKENS,
            chunk_overlap_tokens=VECTOR_STORE_CHUNK_OVERLAP_TOKENS,
        )
        logger.info(f"Stored generated playbook '{playbook_name}' in vector store")
    except Exception:
        logger.exception(f"Failed to store playbook '{playbook_name}' in vector store")
