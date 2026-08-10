from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from helpers import make_state


def _patch_rag_client(mock):
    return patch("agent_service.nodes.rag_retrieval._rag_client", mock)


class TestRagQueryConstruction:
    @pytest.mark.asyncio
    async def test_query_built_from_log_event_fields(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=[])

        with _patch_rag_client(mock):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            state = make_state()
            result = await rag_retrieval_node(state)

        assert "CrashLoopBackOff" in result["rag_query_used"]
        assert "namespace=prod" in result["rag_query_used"]
        assert "pod=nginx-abc123" in result["rag_query_used"]


class TestRagSuccessfulSearch:
    @pytest.mark.asyncio
    async def test_returns_snippets_from_search_results(self):
        mock = MagicMock()
        mock.search = AsyncMock(return_value=["Runbook: restart the pod", "Runbook: check memory limits"])

        with _patch_rag_client(mock):
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
        mock = MagicMock()
        mock.search = AsyncMock(return_value=[])

        with _patch_rag_client(mock):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""


class TestRagErrorHandling:
    @pytest.mark.asyncio
    async def test_client_error_returns_empty_context_no_raise(self):
        mock = MagicMock()
        mock.search = AsyncMock(side_effect=ConnectionError("LlamaStack unreachable"))

        with _patch_rag_client(mock):
            from agent_service.nodes.rag_retrieval import rag_retrieval_node

            result = await rag_retrieval_node(make_state())

        assert result["context_snippets"] == []
        assert result["rag_query_used"] != ""


class TestStoreGeneratedPlaybook:
    @pytest.mark.asyncio
    async def test_stores_composite_document(self):
        mock_file = MagicMock()
        mock_file.id = "file-abc"
        mock_rag = MagicMock()
        mock_rag._resolve_vector_store_id = AsyncMock(return_value="vs-123")
        mock_rag._client.files.create = AsyncMock(return_value=mock_file)
        mock_rag._client.vector_stores.files.create = AsyncMock()

        with _patch_rag_client(mock_rag):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="fix-oom",
                playbook_yaml="- hosts: all\n  tasks: []",
                failure_type="OOMKilled",
                summary="Container killed by OOM",
            )

        file_arg = mock_rag._client.files.create.call_args.kwargs["file"]
        content = file_arg[1]
        expected = (
            "Failure: OOMKilled\n"
            "Summary: Container killed by OOM\n"
            "Playbook (OOMKilled):\n"
            "- hosts: all\n  tasks: []"
        )
        assert content == expected.encode()

    @pytest.mark.asyncio
    async def test_uses_playbook_name_as_filename(self):
        mock_file = MagicMock()
        mock_file.id = "file-abc"
        mock_rag = MagicMock()
        mock_rag._resolve_vector_store_id = AsyncMock(return_value="vs-123")
        mock_rag._client.files.create = AsyncMock(return_value=mock_file)
        mock_rag._client.vector_stores.files.create = AsyncMock()

        with _patch_rag_client(mock_rag):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="remediate-crash",
                playbook_yaml="yaml content",
                failure_type="CrashLoop",
                summary="Pod crashing",
            )

        file_arg = mock_rag._client.files.create.call_args.kwargs["file"]
        assert file_arg[0] == "remediate-crash.md"

    @pytest.mark.asyncio
    async def test_attaches_file_to_vector_store(self):
        mock_file = MagicMock()
        mock_file.id = "file-abc"
        mock_rag = MagicMock()
        mock_rag._resolve_vector_store_id = AsyncMock(return_value="vs-123")
        mock_rag._client.files.create = AsyncMock(return_value=mock_file)
        mock_rag._client.vector_stores.files.create = AsyncMock()

        with _patch_rag_client(mock_rag):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="fix-dns",
                playbook_yaml="tasks: []",
                failure_type="DNSFailure",
                summary="DNS resolution failed",
            )

        mock_rag._client.vector_stores.files.create.assert_awaited_once()
        call = mock_rag._client.vector_stores.files.create.call_args
        assert call[0][0] == "vs-123"
        assert call.kwargs["file_id"] == "file-abc"

    @pytest.mark.asyncio
    async def test_logs_error_on_client_failure(self):
        mock_rag = MagicMock()
        mock_rag._resolve_vector_store_id = AsyncMock(return_value="vs-123")
        mock_rag._client.files.create = AsyncMock(
            side_effect=ConnectionError("upload failed"),
        )

        with _patch_rag_client(mock_rag):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="fix-crash",
                playbook_yaml="tasks: []",
                failure_type="CrashLoop",
                summary="Pod crashing",
            )

    @pytest.mark.asyncio
    async def test_skips_when_vector_store_not_found(self):
        mock_rag = MagicMock()
        mock_rag._resolve_vector_store_id = AsyncMock(return_value=None)

        with _patch_rag_client(mock_rag):
            from agent_service.nodes.rag_retrieval import store_generated_playbook

            await store_generated_playbook(
                playbook_name="fix-net",
                playbook_yaml="tasks: []",
                failure_type="NetworkFailure",
                summary="Network unreachable",
            )

        mock_rag._client.files.create.assert_not_called()
