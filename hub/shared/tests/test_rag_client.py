"""Tests for the shared RAG client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from llama_stack_client import (
    APIConnectionError,
    APIStatusError,
    AsyncLlamaStackClient,
    BadRequestError,
    InternalServerError,
    NotFoundError,
)
from loguru import logger
from shared.rag import RagClient


def store_page(stores, next_page=None):
    page = MagicMock(data=stores)
    page.has_next_page.return_value = next_page is not None
    page.get_next_page = AsyncMock(return_value=next_page)
    return page


def bad_request(path: str, message: str | None = None) -> BadRequestError:
    request = httpx.Request("POST", f"http://llamastack:8321{path}")
    body = {"error": {"message": message}} if message is not None else None
    response = httpx.Response(400, request=request, json=body)
    return BadRequestError(message or response.reason_phrase, response=response, body=body)


def search_results(*texts):
    return SimpleNamespace(data=[SimpleNamespace(content=[SimpleNamespace(text=text)]) for text in texts])


@pytest.fixture
def mock_llamastack():
    mock = MagicMock()
    mock.with_options.return_value = mock
    mock.vector_stores.list = AsyncMock(return_value=store_page([]))
    mock.vector_stores.search = AsyncMock(return_value=MagicMock(data=[]))
    return mock


@pytest.fixture
def rag_client(mock_llamastack):
    client = RagClient(host="llamastack", port=8321, vector_store_name="test_docs")
    client._client = mock_llamastack
    return client


@pytest.fixture
def stores(mock_llamastack):
    catalog: dict[str, list[str] | APIStatusError] = {"vs-live": ["original context"]}

    async def list_stores(**options):
        return store_page([SimpleNamespace(id=store_id, name="test_docs") for store_id in catalog])

    async def search_store(store_id, **options):
        if store_id not in catalog:
            request = httpx.Request("POST", f"http://llamastack:8321/v1/vector_stores/{store_id}/search")
            response = httpx.Response(404, request=request)
            raise NotFoundError(response.reason_phrase, response=response, body=None)
        result = catalog[store_id]
        if isinstance(result, APIStatusError):
            raise result
        return search_results(*result)

    mock_llamastack.vector_stores.list.side_effect = list_stores
    mock_llamastack.vector_stores.search.side_effect = search_store
    return catalog


class TestSearch:
    @pytest.mark.asyncio
    async def test_stale_cached_store_id_resolves_once_and_retries_search(self, rag_client, mock_llamastack, stores):
        assert await rag_client.search("before repair") == ["original context"]
        del stores["vs-live"]
        stores["vs-repaired"] = ["repaired context"]

        assert await rag_client.search("antenna", max_num_results=3, score_threshold=0.4) == ["repaired context"]
        attempts = mock_llamastack.vector_stores.search.await_args_list[1:]
        assert [call.args[0] for call in attempts] == ["vs-live", "vs-repaired"]
        assert attempts[0].kwargs == attempts[1].kwargs

        assert await rag_client.search("again") == ["repaired context"]
        assert mock_llamastack.vector_stores.list.await_count == 2

    @pytest.mark.asyncio
    async def test_search_recovers_when_replacement_appears_later(self, rag_client, mock_llamastack, stores):
        assert await rag_client.search("before removal") == ["original context"]
        stale_search_url = "/v1/vector_stores/vs-live/search"
        stores["vs-live"] = bad_request(stale_search_url, "Vector_store 'vs-live' not found")
        mock_llamastack.vector_stores.list.side_effect = None
        mock_llamastack.vector_stores.list.return_value = store_page([])

        assert await rag_client.search("while store is missing") == []
        assert mock_llamastack.vector_stores.search.await_count == 2

        del stores["vs-live"]
        stores["vs-repaired"] = ["repaired context"]
        replacement = SimpleNamespace(id="vs-repaired", name="test_docs")
        mock_llamastack.vector_stores.list.return_value = store_page([replacement])

        assert await rag_client.search("after recreation") == ["repaired context"]
        assert mock_llamastack.vector_stores.list.await_count == 3

    @pytest.mark.asyncio
    async def test_unrelated_search_error_does_not_retry_or_switch_stores(self, rag_client, mock_llamastack, stores):
        await rag_client.search("populate cache")
        error = bad_request("/v1/vector_stores/vs-live/search")
        stores["vs-live"] = error
        stores["vs-earlier"] = ["other corpus"]

        with pytest.raises(BadRequestError) as raised:
            await rag_client.search("antenna")

        assert raised.value is error
        assert mock_llamastack.vector_stores.list.await_count == 2
        assert mock_llamastack.vector_stores.search.await_count == 2
        stores["vs-live"] = ["recovered context"]
        assert await rag_client.search("next query") == ["recovered context"]

    @pytest.mark.asyncio
    async def test_failed_lookup_preserves_original_search_error(self, rag_client, mock_llamastack, stores):
        await rag_client.search("populate cache")
        error = bad_request("/v1/vector_stores/vs-live/search")
        stores["vs-live"] = error
        mock_llamastack.vector_stores.list.side_effect = APIConnectionError(
            request=httpx.Request("GET", "http://llamastack:8321/v1/vector_stores")
        )

        with pytest.raises(BadRequestError) as raised:
            await rag_client.search("antenna")

        assert raised.value is error
        assert mock_llamastack.vector_stores.list.await_count == 2
        assert mock_llamastack.vector_stores.search.await_count == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("malformed_lookup", [False, True])
    async def test_http_search_error_propagates_without_transport_retry(self, rag_client, malformed_lookup):
        listed_stores = {"data": [{"id": "vs-live", "name": "test_docs"}], "has_more": False}
        search_responses = []

        def respond(request):
            if request.method == "GET":
                return httpx.Response(200, json={} if malformed_lookup and search_responses else listed_stores)
            response = httpx.Response(503, headers={"Retry-After": "0.001"})
            search_responses.append(response)
            return response

        async with AsyncLlamaStackClient(
            base_url="http://llamastack:8321",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            rag_client._client = client
            with pytest.raises(InternalServerError) as raised:
                await rag_client.search("antenna")

        assert len(search_responses) == 1
        assert raised.value.response is search_responses[0]

    @pytest.mark.asyncio
    async def test_retry_failure_propagates_without_another_lookup(self, rag_client, mock_llamastack, stores):
        await rag_client.search("populate cache")
        del stores["vs-live"]
        error = bad_request("/v1/vector_stores/vs-repaired/search")
        stores["vs-repaired"] = error

        with pytest.raises(BadRequestError) as raised:
            await rag_client.search("antenna")

        assert raised.value is error
        assert mock_llamastack.vector_stores.list.await_count == 2
        assert mock_llamastack.vector_stores.search.await_count == 3

    @pytest.mark.asyncio
    async def test_returns_unique_snippets_in_rank_order(self, rag_client, stores):
        stores["vs-live"] = [
            "Doc: check antenna alignment",
            "Doc: verify power settings",
            "Doc: check antenna alignment",
            "Doc: inspect feeder cables",
        ]

        result = await rag_client.search("test query")

        assert result == ["Doc: check antenna alignment", "Doc: verify power settings", "Doc: inspect feeder cables"]

    @pytest.mark.asyncio
    async def test_empty_search_returns_empty_list(self, rag_client, stores):
        stores["vs-live"] = []

        result = await rag_client.search("test query")

        assert result == []

    @pytest.mark.asyncio
    async def test_passes_configurable_search_params(self, rag_client, mock_llamastack, stores):
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
        mock_llamastack.vector_stores.list = AsyncMock(return_value=store_page([mock_vs]))

        await rag_client.search("test query")

        mock_llamastack.vector_stores.list.assert_awaited_once()
        mock_llamastack.vector_stores.search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_caches_resolved_id(self, rag_client, mock_llamastack):
        mock_vs = MagicMock()
        mock_vs.id = "vs-found"
        mock_vs.name = "test_docs"
        mock_llamastack.vector_stores.list = AsyncMock(return_value=store_page([mock_vs]))

        await rag_client.search("query 1")
        await rag_client.search("query 2")

        mock_llamastack.vector_stores.list.assert_awaited_once()
        assert mock_llamastack.vector_stores.search.await_count == 2

    @pytest.mark.asyncio
    async def test_finds_exact_name_beyond_first_page(self, rag_client, mock_llamastack):
        unrelated = MagicMock()
        unrelated.id = "vs-other"
        unrelated.name = "test_docs_old"
        target = MagicMock()
        target.id = "vs-target"
        target.name = "test_docs"
        second_page = store_page([target])
        first_page = store_page([unrelated] * 100, second_page)
        mock_llamastack.vector_stores.list.return_value = first_page

        await rag_client.search("test query")

        first_page.get_next_page.assert_awaited_once()
        assert mock_llamastack.vector_stores.search.call_args.args[0] == "vs-target"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("store_ids", [("vs-a", "vs-z"), ("vs-z", "vs-a")])
    async def test_duplicate_names_warn_and_search_uses_a_stable_store(self, rag_client, mock_llamastack, store_ids):
        first, second = [SimpleNamespace(id=store_id, name="test_docs") for store_id in store_ids]
        mock_llamastack.vector_stores.list.return_value = store_page([first], store_page([second]))

        async def search_store(store_id, **options):
            return search_results(f"Context from {store_id}")

        mock_llamastack.vector_stores.search.side_effect = search_store
        warnings = []
        sink = logger.add(warnings.append, level="WARNING", format="{message}")
        try:
            assert await rag_client.search("test query") == ["Context from vs-a"]
            assert await rag_client.search("next query") == ["Context from vs-a"]
        finally:
            logger.remove(sink)

        assert any(all(value in warning for value in ("test_docs", "vs-a", "vs-z")) for warning in warnings)

    @pytest.mark.asyncio
    async def test_not_found_returns_empty(self, rag_client, mock_llamastack):
        result = await rag_client.search("test query")

        assert result == []
        mock_llamastack.vector_stores.search.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_search_finds_store_created_after_previous_miss(self, rag_client, mock_llamastack):
        await rag_client.search("query 1")
        mock_vs = MagicMock()
        mock_vs.id = "vs-new"
        mock_vs.name = "test_docs"
        mock_llamastack.vector_stores.list.return_value = store_page([mock_vs])
        await rag_client.search("query 2")

        assert mock_llamastack.vector_stores.list.await_count == 2
        mock_llamastack.vector_stores.search.assert_awaited_once()
        assert mock_llamastack.vector_stores.search.call_args.args[0] == "vs-new"


class TestUploadFile:
    @pytest.mark.asyncio
    async def test_upload_rejects_ambiguous_name_even_after_search(self, rag_client, mock_llamastack):
        first = SimpleNamespace(id="vs-a", name="test_docs")
        second = SimpleNamespace(id="vs-z", name="test_docs")
        mock_llamastack.vector_stores.list.return_value = store_page([first, second])

        await rag_client.search("test query")
        with pytest.raises(ValueError, match="Multiple vector stores named 'test_docs'"):
            await rag_client.upload_file("playbook.md", b"content")

        mock_llamastack.files.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_reattaches_to_repaired_store_after_precise_stale_id_error(self, rag_client, mock_llamastack):
        rag_client._upload_store_id = "vs-deleted"
        replacement = MagicMock(id="vs-repaired")
        replacement.name = "test_docs"
        mock_llamastack.vector_stores.list.return_value = store_page([replacement])
        mock_llamastack.files.create = AsyncMock(return_value=MagicMock(id="file-new"))
        mock_llamastack.vector_stores.files.create = AsyncMock(
            side_effect=[bad_request("/v1/vector_stores/vs-deleted/files", "Vector_store 'vs-deleted' not found"), None]
        )

        await rag_client.upload_file("playbook.md", b"content")

        assert [call.args[0] for call in mock_llamastack.vector_stores.files.create.await_args_list] == [
            "vs-deleted", "vs-repaired"
        ]
        mock_llamastack.files.create.assert_awaited_once()
        mock_llamastack.vector_stores.list.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upload_finds_store_created_after_previous_miss(self, rag_client, mock_llamastack):
        await rag_client.upload_file("first.md", b"content")
        mock_vs = MagicMock()
        mock_vs.id = "vs-new"
        mock_vs.name = "test_docs"
        mock_llamastack.vector_stores.list.return_value = store_page([mock_vs])
        mock_llamastack.files.create = AsyncMock(return_value=MagicMock(id="file-new"))
        mock_llamastack.vector_stores.files.create = AsyncMock()

        await rag_client.upload_file("second.md", b"content")

        assert mock_llamastack.vector_stores.list.await_count == 2
        mock_llamastack.vector_stores.files.create.assert_awaited_once()
        assert mock_llamastack.vector_stores.files.create.call_args.args[0] == "vs-new"

    @pytest.mark.asyncio
    async def test_uploads_and_attaches_file(self, rag_client, mock_llamastack):
        store = SimpleNamespace(id="vs-123", name="test_docs")
        mock_llamastack.vector_stores.list.return_value = store_page([store])
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
        store = SimpleNamespace(id="vs-123", name="test_docs")
        mock_llamastack.vector_stores.list.return_value = store_page([store])
        mock_file = MagicMock()
        mock_file.id = "file-abc"
        mock_llamastack.files.create = AsyncMock(return_value=mock_file)
        mock_llamastack.vector_stores.files.create = AsyncMock()

        await rag_client.upload_file("test.md", b"content", chunk_size_tokens=1024, chunk_overlap_tokens=128)

        call = mock_llamastack.vector_stores.files.create.call_args
        strategy = call.kwargs["chunking_strategy"]
        assert strategy["static"]["max_chunk_size_tokens"] == 1024
        assert strategy["static"]["chunk_overlap_tokens"] == 128


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_error_propagates(self, rag_client, mock_llamastack, stores):
        mock_llamastack.vector_stores.search.side_effect = ConnectionError("LlamaStack unreachable")

        with pytest.raises(ConnectionError):
            await rag_client.search("test query")

    @pytest.mark.asyncio
    async def test_list_error_propagates(self, rag_client, mock_llamastack):
        mock_llamastack.vector_stores.list = AsyncMock(side_effect=ConnectionError("LlamaStack unreachable"))

        with pytest.raises(ConnectionError):
            await rag_client.search("test query")
