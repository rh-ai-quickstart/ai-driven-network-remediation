"""Tests for the RAG retrieval node (query construction + error handling)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from helpers import make_state
from ran_rca_service.nodes.rag_retrieval import rag_retrieval_node


def _patch_rag_client(mock):
    return patch("ran_rca_service.nodes.rag_retrieval._rag_client", mock)


class TestQueryConstruction:
    @pytest.mark.asyncio
    async def test_query_built_from_relevant_fields(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=[])

        with _patch_rag_client(mock):
            result = await rag_retrieval_node(make_state())

        assert "Band 29" in result["rag_query_used"]
        assert "LowRsrp" in result["rag_query_used"]
        assert "Low RSRP" in result["rag_query_used"]

    @pytest.mark.asyncio
    async def test_passes_query_to_search(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=[])

        with _patch_rag_client(mock):
            await rag_retrieval_node(make_state(anomaly="High interference on cell 7"))

        query = mock.search.call_args[0][0]
        assert "High interference on cell 7" in query
        assert "LowRsrp" in query


class TestSearchResults:
    @pytest.mark.asyncio
    async def test_returns_snippets_as_context(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=["Doc: check antenna", "Doc: verify power"])

        with _patch_rag_client(mock):
            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == ["Doc: check antenna", "Doc: verify power"]

    @pytest.mark.asyncio
    async def test_empty_search_returns_empty_snippets(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=[])

        with _patch_rag_client(mock):
            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_error_returns_empty_context(self):
        mock = MagicMock()
        mock.search = AsyncMock(side_effect=ConnectionError("LlamaStack unreachable"))

        with _patch_rag_client(mock):
            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""
