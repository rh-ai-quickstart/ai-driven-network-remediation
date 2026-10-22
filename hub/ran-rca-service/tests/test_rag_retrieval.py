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
    async def test_valid_low_confidence_keeps_kpi_evidence_and_prediction(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=[])
        with _patch_rag_client(mock):
            result = await rag_retrieval_node(
                make_state(
                    ml_class="Antenna Failure",
                    ml_class_index=0,
                    ml_confidence=0.4,
                    ml_status="used",
                    ml_steer_used=True,
                )
            )
        assert "Antenna Failure" in result["rag_query_used"]
        assert "TX_Bytes" in result["rag_query_used"]
        assert "zone=A" in result["rag_query_used"]

    @pytest.mark.asyncio
    async def test_confident_ml_hint_steers_search(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=[])
        with _patch_rag_client(mock):
            result = await rag_retrieval_node(
                make_state(
                    ml_class="Antenna Failure",
                    ml_class_index=0,
                    ml_confidence=0.9,
                    ml_status="used",
                    ml_steer_used=True,
                )
            )
        assert "Antenna Failure" in result["rag_query_used"]
        assert "TX_Bytes" in result["rag_query_used"]

    @pytest.mark.asyncio
    async def test_query_built_from_relevant_fields(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=[])

        with _patch_rag_client(mock):
            result = await rag_retrieval_node(make_state())

        assert "zone=A" in result["rag_query_used"]
        assert "Twitch" in result["rag_query_used"]
        assert "0.94" in result["rag_query_used"]

    @pytest.mark.asyncio
    async def test_passes_query_to_search(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=[])

        with _patch_rag_client(mock):
            await rag_retrieval_node(make_state(application="YouTube"))

        query = mock.search.call_args[0][0]
        assert "YouTube" in query


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
    async def test_search_error_propagates(self):
        mock = MagicMock()
        mock.search = AsyncMock(side_effect=ConnectionError("LlamaStack unreachable"))

        with _patch_rag_client(mock):
            with pytest.raises(ConnectionError):
                await rag_retrieval_node(make_state())
