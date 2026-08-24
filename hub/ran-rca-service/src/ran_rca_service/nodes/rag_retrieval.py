"""RAG retrieval node — queries the telco_oran_docs vector store via shared RagClient."""

from __future__ import annotations

from loguru import logger
from ran_rca_service.config import LLAMASTACK_HOST, LLAMASTACK_PORT, VECTOR_STORE_NAME
from ran_rca_service.models import RCAState
from shared.rag import RagClient

_rag_client: RagClient | None = None


def _get_rag_client() -> RagClient:
    global _rag_client
    if _rag_client is None:
        _rag_client = RagClient(
            host=LLAMASTACK_HOST,
            port=LLAMASTACK_PORT,
            vector_store_name=VECTOR_STORE_NAME,
        )
    return _rag_client


async def rag_retrieval_node(state: RCAState) -> dict:
    if state.ml_steer_used and state.ml_root_cause_class:
        query = f"{state.ml_root_cause_class} {state.anomaly_type} {state.band}"
    else:
        query = f"{state.anomaly_type} {state.anomaly} {state.band}"
    try:
        snippets = await _get_rag_client().search(query)
        return {"context_snippets": snippets, "rag_query_used": query}
    except Exception:
        logger.exception("LlamaStack search failed")
        return {"context_snippets": [], "rag_query_used": query}
