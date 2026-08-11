"""Tests for the shared RAG client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.rag import RagClient


@pytest.fixture
def mock_llamastack():
    mock = MagicMock()
    mock.vector_stores.list = AsyncMock(return_value=MagicMock(data=[]))
    mock.vector_stores.search = AsyncMock(return_value=MagicMock(data=[]))
    return mock


@pytest.fixture
def rag_client(mock_llamastack):
    client = RagClient(host="llamastack", port=8321, vector_store_name="test_docs")
    client._client = mock_llamastack
    return client


class TestSearch:
    @pytest.mark.asyncio
    async def test_returns_snippets_from_search_results(self, rag_client, mock_llamastack):
        rag_client._vector_store_id = "vs-123"
        mock_content_1 = MagicMock(text="Doc: check antenna alignment")
        mock_content_2 = MagicMock(text="Doc: verify power settings")
        mock_item_1 = MagicMock(content=[mock_content_1])
        mock_item_2 = MagicMock(content=[mock_content_2])
        mock_llamastack.vector_stores.search = AsyncMock(
            return_value=MagicMock(data=[mock_item_1, mock_item_2])
        )

        result = await rag_client.search("test query")

        assert result == ["Doc: check antenna alignment", "Doc: verify power settings"]

    @pytest.mark.asyncio
    async def test_empty_search_returns_empty_list(self, rag_client):
        rag_client._vector_store_id = "vs-123"

        result = await rag_client.search("test query")

        assert result == []

    @pytest.mark.asyncio
    async def test_passes_configurable_search_params(self, rag_client, mock_llamastack):
        rag_client._vector_store_id = "vs-123"

        await rag_client.search("test query", max_num_results=10, score_threshold=0.5)

        call_args = mock_llamastack.vector_stores.search.call_args
        assert call_args.kwargs["max_num_results"] == 10
        assert call_args.kwargs["ranking_options"] == {"score_threshold": 0.5}


class TestVectorStoreResolution:
    @pytest.mark.asyncio
    async def test_resolves_vector_store_by_name(self, rag_client, mock_llamastack):
        mock_vs = MagicMock()
        mock_vs.id = "vs-found"
        mock_vs.name = "test_docs"
        mock_llamastack.vector_stores.list = AsyncMock(return_value=MagicMock(data=[mock_vs]))

        await rag_client.search("test query")

        mock_llamastack.vector_stores.list.assert_awaited_once()
        mock_llamastack.vector_stores.search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_caches_resolved_id(self, rag_client, mock_llamastack):
        mock_vs = MagicMock()
        mock_vs.id = "vs-found"
        mock_vs.name = "test_docs"
        mock_llamastack.vector_stores.list = AsyncMock(return_value=MagicMock(data=[mock_vs]))

        await rag_client.search("query 1")
        await rag_client.search("query 2")

        mock_llamastack.vector_stores.list.assert_awaited_once()
        assert mock_llamastack.vector_stores.search.await_count == 2

    @pytest.mark.asyncio
    async def test_not_found_returns_empty(self, rag_client, mock_llamastack):
        result = await rag_client.search("test query")

        assert result == []
        mock_llamastack.vector_stores.search.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_negative_cache_prevents_repeated_lookups(self, rag_client, mock_llamastack):
        await rag_client.search("query 1")
        await rag_client.search("query 2")

        mock_llamastack.vector_stores.list.assert_awaited_once()


class TestUploadFile:
    @pytest.mark.asyncio
    async def test_uploads_and_attaches_file(self, rag_client, mock_llamastack):
        rag_client._vector_store_id = "vs-123"
        mock_file = MagicMock()
        mock_file.id = "file-abc"
        mock_llamastack.files.create = AsyncMock(return_value=mock_file)
        mock_llamastack.vector_stores.files.create = AsyncMock()

        await rag_client.upload_file("test.md", b"hello world")

        mock_llamastack.files.create.assert_awaited_once()
        file_arg = mock_llamastack.files.create.call_args.kwargs["file"]
        assert file_arg[0] == "test.md"
        assert file_arg[1] == b"hello world"

        mock_llamastack.vector_stores.files.create.assert_awaited_once()
        call = mock_llamastack.vector_stores.files.create.call_args
        assert call[0][0] == "vs-123"
        assert call.kwargs["file_id"] == "file-abc"

    @pytest.mark.asyncio
    async def test_skips_when_vector_store_not_found(self, rag_client, mock_llamastack):
        mock_llamastack.files.create = AsyncMock()

        await rag_client.upload_file("test.md", b"content")

        mock_llamastack.files.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passes_chunking_strategy(self, rag_client, mock_llamastack):
        rag_client._vector_store_id = "vs-123"
        mock_file = MagicMock()
        mock_file.id = "file-abc"
        mock_llamastack.files.create = AsyncMock(return_value=mock_file)
        mock_llamastack.vector_stores.files.create = AsyncMock()

        await rag_client.upload_file(
            "test.md", b"content", chunk_size_tokens=1024, chunk_overlap_tokens=128
        )

        call = mock_llamastack.vector_stores.files.create.call_args
        strategy = call.kwargs["chunking_strategy"]
        assert strategy["static"]["max_chunk_size_tokens"] == 1024
        assert strategy["static"]["chunk_overlap_tokens"] == 128


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_error_propagates(self, rag_client, mock_llamastack):
        rag_client._vector_store_id = "vs-123"
        mock_llamastack.vector_stores.search = AsyncMock(
            side_effect=ConnectionError("LlamaStack unreachable")
        )

        with pytest.raises(ConnectionError):
            await rag_client.search("test query")

    @pytest.mark.asyncio
    async def test_list_error_propagates(self, rag_client, mock_llamastack):
        mock_llamastack.vector_stores.list = AsyncMock(
            side_effect=ConnectionError("LlamaStack unreachable")
        )

        with pytest.raises(ConnectionError):
            await rag_client.search("test query")
