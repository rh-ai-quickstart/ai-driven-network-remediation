"""Tests for the real RAG retrieval node (LlamaStack vector store query)."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from helpers import make_state
from ran_rca_service.nodes.rag_retrieval import rag_retrieval_node


@contextmanager
def patch_rag_client(mock_client, *, vector_store_id="vs-123", resolved=True):
    patched_id = vector_store_id if resolved else None
    with (
        patch("ran_rca_service.nodes.rag_retrieval._client", mock_client),
        patch("ran_rca_service.nodes.rag_retrieval._vector_store_id", patched_id),
        patch("ran_rca_service.nodes.rag_retrieval._negative_cache_until", 0.0),
    ):
        yield


class TestRagQueryConstruction:
    @pytest.mark.asyncio
    async def test_query_built_from_relevant_fields(self):
        mock_client = MagicMock()
        mock_client.vector_stores.list = AsyncMock(return_value=MagicMock(data=[]))
        mock_client.vector_stores.search = AsyncMock(return_value=MagicMock(data=[]))

        with patch_rag_client(mock_client):
            state = make_state()
            result = await rag_retrieval_node(state)

        assert "Band 29" in result["rag_query_used"]
        assert "LowRsrp" in result["rag_query_used"]
        assert "Low RSRP" in result["rag_query_used"]
        assert "root_cause" not in result["rag_query_used"]

    @pytest.mark.asyncio
    async def test_passes_query_to_search(self):
        mock_client = MagicMock()
        mock_client.vector_stores.search = AsyncMock(return_value=MagicMock(data=[]))

        with patch_rag_client(mock_client):
            state = make_state(anomaly="High interference on cell 7")
            await rag_retrieval_node(state)

        mock_client.vector_stores.search.assert_awaited_once()
        call_kwargs = mock_client.vector_stores.search.call_args
        query = call_kwargs.kwargs["query"]
        assert "High interference on cell 7" in query
        assert "LowRsrp" in query


class TestRagSuccessfulSearch:
    @pytest.mark.asyncio
    async def test_returns_snippets_from_search_results(self):
        mock_content_1 = MagicMock(text="Doc: check antenna alignment")
        mock_content_2 = MagicMock(text="Doc: verify power settings")
        mock_item_1 = MagicMock(content=[mock_content_1])
        mock_item_2 = MagicMock(content=[mock_content_2])
        mock_response = MagicMock(data=[mock_item_1, mock_item_2])

        mock_client = MagicMock()
        mock_client.vector_stores.search = AsyncMock(return_value=mock_response)

        with patch_rag_client(mock_client):
            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == [
            "Doc: check antenna alignment",
            "Doc: verify power settings",
        ]
        assert result["rag_query_used"] != ""


class TestRagEmptyResults:
    @pytest.mark.asyncio
    async def test_empty_search_returns_empty_snippets(self):
        mock_client = MagicMock()
        mock_client.vector_stores.search = AsyncMock(return_value=MagicMock(data=[]))

        with patch_rag_client(mock_client):
            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""


class TestRagVectorStoreLookup:
    @pytest.mark.asyncio
    async def test_looks_up_vector_store_by_name_on_first_call(self):
        mock_vs = MagicMock()
        mock_vs.id = "vs-found"
        mock_vs.name = "telco_oran_docs"
        mock_client = MagicMock()
        mock_client.vector_stores.list = AsyncMock(return_value=MagicMock(data=[mock_vs]))
        mock_client.vector_stores.search = AsyncMock(return_value=MagicMock(data=[]))

        with patch_rag_client(mock_client, resolved=False):
            await rag_retrieval_node(make_state())

        mock_client.vector_stores.list.assert_awaited_once()
        mock_client.vector_stores.search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_vector_store_not_found_returns_empty(self):
        mock_client = MagicMock()
        mock_client.vector_stores.list = AsyncMock(return_value=MagicMock(data=[]))

        with patch_rag_client(mock_client, resolved=False):
            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""


class TestRagErrorHandling:
    @pytest.mark.asyncio
    async def test_client_error_returns_empty_context_no_raise(self):
        mock_client = MagicMock()
        mock_client.vector_stores.search = AsyncMock(
            side_effect=ConnectionError("LlamaStack unreachable"),
        )

        with patch_rag_client(mock_client):
            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""

    @pytest.mark.asyncio
    async def test_vector_store_lookup_error_returns_empty_context(self):
        mock_client = MagicMock()
        mock_client.vector_stores.list = AsyncMock(
            side_effect=ConnectionError("LlamaStack unreachable"),
        )

        with patch_rag_client(mock_client, resolved=False):
            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""
