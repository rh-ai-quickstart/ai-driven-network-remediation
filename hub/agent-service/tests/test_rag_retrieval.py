from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from helpers import make_state


@contextmanager
def patch_rag_client(mock_client, *, vector_store_id="vs-123", resolved=True):
    patched_id = vector_store_id if resolved else None
    with (
        patch("agent_service.nodes.rag_retrieval._client", mock_client),
        patch("agent_service.nodes.rag_retrieval._vector_store_id", patched_id),
        patch("agent_service.nodes.rag_retrieval._negative_cache_until", 0.0),
    ):
        yield


class TestRagQueryConstruction:
    @pytest.mark.asyncio
    async def test_query_built_from_log_event_fields(self):
        mock_client = MagicMock()
        mock_client.vector_stores.list = AsyncMock(return_value=MagicMock(data=[]))
        mock_client.vector_stores.search = AsyncMock(return_value=MagicMock(data=[]))

        with patch_rag_client(mock_client):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            state = make_state()
            result = await rag_retrieval_node(state)

        assert "CrashLoopBackOff" in result["rag_query_used"]
        assert "namespace=prod" in result["rag_query_used"]
        assert "pod=nginx-abc123" in result["rag_query_used"]


class TestRagSuccessfulSearch:
    @pytest.mark.asyncio
    async def test_returns_snippets_from_search_results(self):
        mock_content_1 = MagicMock(text="Runbook: restart the pod")
        mock_content_2 = MagicMock(text="Runbook: check memory limits")
        mock_item_1 = MagicMock(content=[mock_content_1])
        mock_item_2 = MagicMock(content=[mock_content_2])
        mock_response = MagicMock(data=[mock_item_1, mock_item_2])

        mock_client = MagicMock()
        mock_client.vector_stores.search = AsyncMock(return_value=mock_response)

        with patch_rag_client(mock_client):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == [
            "Runbook: restart the pod",
            "Runbook: check memory limits",
        ]
        assert result["rag_query_used"] != ""


class TestRagEmptyResults:
    @pytest.mark.asyncio
    async def test_empty_search_returns_empty_snippets(self):
        mock_client = MagicMock()
        mock_client.vector_stores.search = AsyncMock(return_value=MagicMock(data=[]))

        with patch_rag_client(mock_client):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""


class TestRagVectorStoreLookup:
    @pytest.mark.asyncio
    async def test_looks_up_vector_store_by_name_on_first_call(self):
        mock_vs = MagicMock()
        mock_vs.id = "vs-found"
        mock_vs.name = "noc_runbooks"
        mock_client = MagicMock()
        mock_client.vector_stores.list = AsyncMock(return_value=MagicMock(data=[mock_vs]))
        mock_client.vector_stores.search = AsyncMock(return_value=MagicMock(data=[]))

        with patch_rag_client(mock_client, resolved=False):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            await rag_retrieval_node(make_state())

        mock_client.vector_stores.list.assert_awaited_once()
        mock_client.vector_stores.search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_vector_store_not_found_returns_empty(self):
        mock_client = MagicMock()
        mock_client.vector_stores.list = AsyncMock(return_value=MagicMock(data=[]))

        with patch_rag_client(mock_client, resolved=False):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""


class TestRagErrorHandling:
    @pytest.mark.asyncio
    async def test_client_error_returns_empty_context_no_raise(self):
        mock_client = MagicMock()
        mock_client.vector_stores.search = AsyncMock(side_effect=ConnectionError("LlamaStack unreachable"))

        with patch_rag_client(mock_client):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""

    @pytest.mark.asyncio
    async def test_vector_store_lookup_error_returns_empty_context(self):
        mock_client = MagicMock()
        mock_client.vector_stores.list = AsyncMock(side_effect=ConnectionError("LlamaStack unreachable"))

        with patch_rag_client(mock_client, resolved=False):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""


class TestStoreGeneratedPlaybook:
    @pytest.mark.asyncio
    async def test_stores_composite_document(self):
        mock_file = MagicMock()
        mock_file.id = "file-abc"
        mock_client = MagicMock()
        mock_client.files.create = AsyncMock(return_value=mock_file)
        mock_client.vector_stores.files.create = AsyncMock()

        with patch_rag_client(mock_client):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="fix-oom",
                playbook_yaml="- hosts: all\n  tasks: []",
                failure_type="OOMKilled",
                summary="Container killed by OOM",
            )

        file_arg = mock_client.files.create.call_args.kwargs["file"]
        content = file_arg[1]
        expected = "Failure: OOMKilled\n" "Summary: Container killed by OOM\n" "Playbook (OOMKilled):\n" "- hosts: all\n  tasks: []"
        assert content == expected.encode()

    @pytest.mark.asyncio
    async def test_uses_playbook_name_as_filename(self):
        mock_file = MagicMock()
        mock_file.id = "file-abc"
        mock_client = MagicMock()
        mock_client.files.create = AsyncMock(return_value=mock_file)
        mock_client.vector_stores.files.create = AsyncMock()

        with patch_rag_client(mock_client):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="remediate-crash",
                playbook_yaml="yaml content",
                failure_type="CrashLoop",
                summary="Pod crashing",
            )

        file_arg = mock_client.files.create.call_args.kwargs["file"]
        assert file_arg[0] == "remediate-crash.md"

    @pytest.mark.asyncio
    async def test_attaches_file_to_vector_store(self):
        mock_file = MagicMock()
        mock_file.id = "file-abc"
        mock_client = MagicMock()
        mock_client.files.create = AsyncMock(return_value=mock_file)
        mock_client.vector_stores.files.create = AsyncMock()

        with patch_rag_client(mock_client):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="fix-dns",
                playbook_yaml="tasks: []",
                failure_type="DNSFailure",
                summary="DNS resolution failed",
            )

        mock_client.vector_stores.files.create.assert_awaited_once()
        call = mock_client.vector_stores.files.create.call_args
        assert call[0][0] == "vs-123"
        assert call.kwargs["file_id"] == "file-abc"

    @pytest.mark.asyncio
    async def test_logs_error_on_client_failure(self):
        mock_client = MagicMock()
        mock_client.files.create = AsyncMock(
            side_effect=ConnectionError("upload failed"),
        )

        with patch_rag_client(mock_client):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="fix-crash",
                playbook_yaml="tasks: []",
                failure_type="CrashLoop",
                summary="Pod crashing",
            )

    @pytest.mark.asyncio
    async def test_skips_when_vector_store_not_found(self):
        mock_client = MagicMock()
        mock_client.files.create = AsyncMock()

        with patch_rag_client(mock_client, vector_store_id=None):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="fix-net",
                playbook_yaml="tasks: []",
                failure_type="NetworkFailure",
                summary="Network unreachable",
            )

        mock_client.files.create.assert_not_called()
